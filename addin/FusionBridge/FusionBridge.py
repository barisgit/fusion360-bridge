"""FusionBridge: localhost HTTP bridge for AI agents.

Runs inside Fusion 360's embedded Python. Exposes:
  GET  /health      -> {ok, fusion_version, document}
  POST /execute     -> body {"script": "..."} runs Python on Fusion's main
                       thread with adsk.core/adsk.fusion preloaded. Returns
                       {ok, stdout, error}.
  GET  /screenshot  -> PNG of the active viewport.

Auth: Bearer token from $XDG_STATE_HOME/fusion-bridge/secret
(default ~/.local/state/fusion-bridge/secret), auto-created on first run.
Bind: 127.0.0.1:7654 only.

Fusion's API is not thread-safe: the HTTP server runs on a background
thread and marshals each request onto the main thread via a CustomEvent,
then waits for the result.
"""

import adsk.core
import adsk.fusion
import contextlib
import io
import json
import os
import secrets
import threading
import traceback
import queue
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 7654
EVENT_ID = "FusionBridge_execute"


def _secret_path():
    state_home = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return os.path.join(state_home, "fusion-bridge", "secret")


SECRET_PATH = _secret_path()

_app = None
_handlers = []  # keep refs so Fusion's GC doesn't collect event handlers
_server = None
_server_thread = None
_custom_event = None

# Work marshaling: HTTP thread puts (job, reply_queue), fires custom event;
# main thread executes and replies.
_jobs = queue.Queue()


def _load_or_create_secret():
    if os.path.exists(SECRET_PATH):
        with open(SECRET_PATH) as f:
            return f.read().strip()
    os.makedirs(os.path.dirname(SECRET_PATH), mode=0o700, exist_ok=True)
    token = secrets.token_hex(32)
    fd = os.open(SECRET_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(token)
    return token


_TOKEN = None


class _ExecuteHandler(adsk.core.CustomEventHandler):
    """Runs on Fusion's main thread when the custom event fires."""

    def notify(self, args):
        while True:
            try:
                job, reply = _jobs.get_nowait()
            except queue.Empty:
                return
            result = {}
            try:
                if job["kind"] == "execute":
                    result = _run_script(job["script"])
                elif job["kind"] == "screenshot":
                    result = _take_screenshot()
                elif job["kind"] == "health":
                    result = _health()
            except Exception:
                result = {"ok": False, "error": traceback.format_exc()}
            reply.put(result)


def _run_script(script):
    env = {
        "adsk": adsk,
        "app": adsk.core.Application.get(),
        "ui": adsk.core.Application.get().userInterface,
    }
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            exec(script, env)
        return {"ok": True, "stdout": buf.getvalue()}
    except Exception:
        return {"ok": False, "stdout": buf.getvalue(), "error": traceback.format_exc()}


def _take_screenshot():
    import tempfile

    app = adsk.core.Application.get()
    path = os.path.join(tempfile.gettempdir(), "fusion_bridge_shot.png")
    vp = app.activeViewport
    if vp is None:
        return {"ok": False, "error": "no active viewport"}
    vp.saveAsImageFile(path, 0, 0)  # 0,0 = current viewport size
    with open(path, "rb") as f:
        data = f.read()
    return {"ok": True, "_png": data}


def _health():
    app = adsk.core.Application.get()
    doc = app.activeDocument
    return {
        "ok": True,
        "fusion_version": app.version,
        "document": doc.name if doc else None,
    }


def _dispatch(job, timeout=120):
    """Called from HTTP thread: queue job, fire event, wait for reply."""
    reply = queue.Queue()
    _jobs.put((job, reply))
    _app.fireCustomEvent(EVENT_ID)
    try:
        return reply.get(timeout=timeout)
    except queue.Empty:
        return {"ok": False, "error": "timeout waiting for Fusion main thread"}


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _authed(self):
        auth = self.headers.get("Authorization", "")
        return auth == f"Bearer {_TOKEN}"

    def _send_json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self._authed():
            return self._send_json(401, {"ok": False, "error": "unauthorized"})
        if self.path == "/health":
            return self._send_json(200, _dispatch({"kind": "health"}, timeout=10))
        if self.path == "/screenshot":
            res = _dispatch({"kind": "screenshot"}, timeout=30)
            if res.get("ok") and "_png" in res:
                png = res["_png"]
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(png)))
                self.end_headers()
                self.wfile.write(png)
                return
            return self._send_json(500, {k: v for k, v in res.items() if k != "_png"})
        return self._send_json(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        if not self._authed():
            return self._send_json(401, {"ok": False, "error": "unauthorized"})
        if self.path != "/execute":
            return self._send_json(404, {"ok": False, "error": "not found"})
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length) or b"{}")
            script = payload["script"]
        except Exception:
            return self._send_json(400, {"ok": False, "error": "bad request: expected JSON {script}"})
        return self._send_json(200, _dispatch({"kind": "execute", "script": script}))


def run(context):
    global _app, _custom_event, _server, _server_thread, _TOKEN
    try:
        _app = adsk.core.Application.get()
        _TOKEN = _load_or_create_secret()

        # Clean up a stale event from a previous (crashed) run.
        with contextlib.suppress(Exception):
            _app.unregisterCustomEvent(EVENT_ID)

        _custom_event = _app.registerCustomEvent(EVENT_ID)
        handler = _ExecuteHandler()
        _custom_event.add(handler)
        _handlers.append(handler)

        _server = ThreadingHTTPServer(("127.0.0.1", PORT), _Handler)
        _server_thread = threading.Thread(target=_server.serve_forever, daemon=True)
        _server_thread.start()
    except Exception:
        adsk.core.Application.get().userInterface.messageBox(
            "FusionBridge failed to start:\n" + traceback.format_exc()
        )


def stop(context):
    global _server
    with contextlib.suppress(Exception):
        if _server:
            _server.shutdown()
            _server = None
    with contextlib.suppress(Exception):
        _app.unregisterCustomEvent(EVENT_ID)
    _handlers.clear()
