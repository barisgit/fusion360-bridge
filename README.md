# fusion360-bridge

Minimal open replacement for AuraFriday's Fusion-360-MCP-Server: control
Autodesk Fusion 360 from any AI agent, no proprietary daemon.

Two small parts:

- `addin/FusionBridge/` — Fusion 360 add-in. Localhost HTTP API on
  `127.0.0.1:7654` with bearer-token auth (`~/.local/state/fusion-bridge/secret`,
  auto-generated). Endpoints: `POST /execute` (run arbitrary Python on
  Fusion's main thread), `GET /screenshot`, `GET /health`.
- `server/fusion_mcp.py` — stdio MCP server (PEP 723 script, run with
  `uv run`) exposing `fusion_execute_python`, `fusion_screenshot`,
  `fusion_health`. Works with any MCP client. Optional: the HTTP API is
  curl-able directly, so skills/CLIs don't need MCP at all.

## Install

1. Symlink the add-in where Fusion looks for add-ins:

   ```bash
   ln -s "$(pwd)/addin/FusionBridge" \
     "$HOME/Library/Application Support/Autodesk/Autodesk Fusion 360/API/AddIns/FusionBridge"
   ```

2. In Fusion 360: Shift+S -> Add-Ins tab -> FusionBridge -> Run
   (check "Run on Startup").

3. MCP client config:

   ```json
   "fusion360": {
     "command": "uv",
     "args": ["run", "/ABSOLUTE/PATH/TO/server/fusion_mcp.py"]
   }
   ```

## Curl usage (no MCP)

```bash
TOKEN=$(cat ~/.local/state/fusion-bridge/secret)
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:7654/health
curl -s -H "Authorization: Bearer $TOKEN" -X POST http://127.0.0.1:7654/execute \
  -d '{"script": "print(app.version)"}'
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:7654/screenshot -o shot.png
```

## Notes

- Fusion has no headless mode; Fusion must be open with the add-in running.
- The Fusion API is not thread-safe: the HTTP listener marshals every job
  onto the main thread via `adsk.core.CustomEvent` and waits for the reply.
- `/execute` runs arbitrary code inside Fusion by design. The bearer token
  and 127.0.0.1 bind are the protection; don't weaken them.

## Portable install (any machine)

Add to any MCP client config (pin a tag for reproducible installs):

```json
"fusion360": {
  "command": "uvx",
  "args": ["--from", "git+https://github.com/barisgit/fusion360-bridge@v0.2.1",
           "fusion360-bridge", "serve"]
}
```

That's it: on first `serve` the bundled add-in is auto-installed into
Fusion's AddIns folder if missing (the manifest has `runOnStartup`, so the
next Fusion launch activates it). Manual commands if you prefer:

```bash
uvx --from git+https://github.com/barisgit/fusion360-bridge@v0.2.1 fusion360-bridge install-addin
uvx --from git+https://github.com/barisgit/fusion360-bridge@v0.2.1 fusion360-bridge health
```

### Updating

`uvx` caches git builds. To pick up a new version, bump the pinned tag, or
if tracking the default branch, force a re-fetch:

```bash
uvx --refresh --from git+https://github.com/barisgit/fusion360-bridge fusion360-bridge health
```
