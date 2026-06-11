"""FusionBridge: localhost HTTP bridge for AI agents.

Runs inside Fusion 360's embedded Python. Exposes:
  GET  /health      -> {ok, fusion_version, document}
  POST /execute     -> body {"script": "..."} runs Python on Fusion's main
                       thread with adsk.core/adsk.fusion preloaded. Returns
                       {ok, stdout, error}.
  GET  /screenshot  -> PNG of the active viewport.
  GET  /docs?q=...  -> search the adsk API by name: classes, members,
                       signatures, docstrings (introspection, offline).

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
                    result = _take_screenshot(job.get("width", 0), job.get("height", 0))
                elif job["kind"] == "health":
                    result = _health()
                elif job["kind"] == "docs":
                    result = _api_docs(job["query"], job.get("member"))
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


def _take_screenshot(width=0, height=0):
    import tempfile

    app = adsk.core.Application.get()
    fd, path = tempfile.mkstemp(prefix="fusion_bridge_", suffix=".png")
    os.close(fd)
    vp = app.activeViewport
    if vp is None:
        return {"ok": False, "error": "no active viewport"}
    try:
        vp.saveAsImageFile(path, width, height)  # 0,0 = current viewport size
        with open(path, "rb") as f:
            data = f.read()
    finally:
        with contextlib.suppress(OSError):
            os.remove(path)
    return {"ok": True, "_png": data}


def _api_docs(query, member=None, max_results=8):
    """Introspect adsk.core/adsk.fusion/adsk.cam for classes matching query."""
    import inspect

    import importlib

    q = query.lower()
    mods = [adsk.core, adsk.fusion]
    try:
        mods.append(importlib.import_module("adsk.cam"))
    except ImportError:
        pass

    matches = []
    for mod in mods:
        for name, obj in vars(mod).items():
            if inspect.isclass(obj) and q in name.lower():
                matches.append((name, obj, mod.__name__))
    # exact match first, then shorter names first
    matches.sort(key=lambda m: (m[0].lower() != q, len(m[0])))
    matches = matches[:max_results]

    out = []
    for name, cls, modname in matches:
        entry = {"class": f"{modname}.{name}", "doc": inspect.getdoc(cls) or ""}
        members = []
        for mname, mobj in inspect.getmembers(cls):
            if mname.startswith("_"):
                continue
            if member and member.lower() not in mname.lower():
                continue
            kind = "property" if isinstance(mobj, property) else "method"
            mdoc = inspect.getdoc(mobj) or ""
            members.append({"name": mname, "kind": kind, "doc": mdoc[:500]})
        if member:
            entry["members"] = members
        else:
            entry["members"] = [m["name"] for m in members]
        out.append(entry)
    return {"ok": True, "results": out}


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
        from urllib.parse import urlparse, parse_qs

        parsed = urlparse(self.path)
        if parsed.path == "/health":
            return self._send_json(200, _dispatch({"kind": "health"}, timeout=10))
        if parsed.path == "/docs":
            q = parse_qs(parsed.query)
            query = (q.get("q") or [""])[0]
            if not query:
                return self._send_json(400, {"ok": False, "error": "missing ?q="})
            member = (q.get("member") or [None])[0]
            return self._send_json(
                200, _dispatch({"kind": "docs", "query": query, "member": member}, timeout=20)
            )
        if parsed.path == "/screenshot":
            q = parse_qs(parsed.query)

            def _int(name):
                try:
                    return max(0, int(q[name][0]))
                except (KeyError, ValueError, IndexError):
                    return 0

            res = _dispatch(
                {"kind": "screenshot", "width": _int("width"), "height": _int("height")},
                timeout=30,
            )
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
            _server.server_close()
            _server = None
    with contextlib.suppress(Exception):
        _app.unregisterCustomEvent(EVENT_ID)
    _handlers.clear()
