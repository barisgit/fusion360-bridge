# fusion360-bridge

Control Autodesk Fusion 360 from any AI agent: a minimal, fully open
bridge with no external daemon.

Two small parts:

- **Fusion add-in** (`addin/FusionBridge/`) — localhost HTTP API on
  `127.0.0.1:7654`: run arbitrary Python on Fusion's main thread, capture
  viewport screenshots, health check. Bearer-token auth, auto-generated at
  `~/.local/state/fusion-bridge/secret` (honors `$XDG_STATE_HOME`).
- **stdio MCP server** (`src/fusion360_bridge/`) — exposes
  `fusion_execute_python`, `fusion_screenshot`, `fusion_health` to any MCP
  client and forwards to the add-in over HTTP.

## Quick start

Add to your MCP client config (pin a tag for reproducible installs):

```json
"fusion360": {
  "command": "uvx",
  "args": ["--from", "git+https://github.com/barisgit/fusion360-bridge@v0.2.2",
           "fusion360-bridge", "serve"]
}
```

On first `serve` the add-in is auto-installed into Fusion's AddIns folder.
Restart Fusion 360 (or Shift+S -> Add-Ins -> FusionBridge -> Run) and
you're done: Fusion must simply be open for the tools to work.

### CLI

```bash
uvx --from git+https://github.com/barisgit/fusion360-bridge fusion360-bridge install-addin  # explicit add-in install
uvx --from git+https://github.com/barisgit/fusion360-bridge fusion360-bridge health         # connectivity check
```

### Updating

`uvx` caches git builds: bump the pinned tag, or force a re-fetch with
`uvx --refresh --from git+... fusion360-bridge health`.

## HTTP API (no MCP needed)

The add-in itself is plain HTTP, so scripts and skills can skip MCP:

```bash
TOKEN=$(cat ~/.local/state/fusion-bridge/secret)
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:7654/health
curl -s -H "Authorization: Bearer $TOKEN" -X POST http://127.0.0.1:7654/execute \
  -d '{"script": "print(app.version)"}'
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:7654/screenshot -o shot.png
```

Scripts run with `adsk`, `app`, and `ui` preloaded; `print()` output is
captured and returned as JSON `{ok, stdout, error}`.

## Development

Clone, then symlink the add-in instead of copying (auto-install skips
symlinks, so your checkout stays the live add-in):

```bash
ln -s "$(pwd)/addin/FusionBridge" \
  "$HOME/Library/Application Support/Autodesk/Autodesk Fusion 360/API/AddIns/FusionBridge"
```

MCP config for the local checkout:

```json
"fusion360": {
  "command": "uv",
  "args": ["run", "--project", "/path/to/fusion360-bridge", "fusion360-bridge", "serve"]
}
```

Releases are automated: bump `version` in `pyproject.toml`, push to
`main`, and CI tags and publishes the GitHub release with built wheels.

## Notes

- Fusion has no headless mode; Fusion must be open with the add-in running.
- The Fusion API is not thread-safe: the HTTP listener marshals every job
  onto the main thread via `adsk.core.CustomEvent` and waits for the reply.
- `/execute` runs arbitrary code inside Fusion by design. The bearer token
  and 127.0.0.1 bind are the protection; don't weaken them.
- macOS and Windows (Fusion's supported platforms); add-in paths are
  resolved per-OS by the CLI.
