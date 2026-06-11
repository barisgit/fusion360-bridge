"""Stdio MCP server bridging to the FusionBridge add-in over localhost HTTP.

Run with any MCP client as a stdio subprocess:
    uvx --from git+https://github.com/barisgit/fusion360-bridge fusion360-bridge serve

Requires Fusion 360 running with the FusionBridge add-in active.
"""

import base64
import os

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.utilities.types import Image

HOST = "http://127.0.0.1:7654"
_STATE_HOME = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
SECRET_PATH = os.path.join(_STATE_HOME, "fusion-bridge", "secret")

mcp = FastMCP("fusion360")


def _headers():
    try:
        with open(SECRET_PATH) as f:
            token = f.read().strip()
    except FileNotFoundError:
        raise RuntimeError(
            f"{SECRET_PATH} not found. Start Fusion 360 with the FusionBridge "
            "add-in once to generate it."
        )
    return {"Authorization": f"Bearer {token}"}


NOT_RUNNING = (
    "Could not reach FusionBridge at {host}. Is Fusion 360 running with the "
    "FusionBridge add-in enabled (Shift+S -> Add-Ins -> FusionBridge -> Run)?"
)


@mcp.tool()
def fusion_health() -> str:
    """Check the bridge: returns Fusion version and active document name."""
    try:
        r = httpx.get(f"{HOST}/health", headers=_headers(), timeout=15)
        return r.text
    except httpx.ConnectError:
        return NOT_RUNNING.format(host=HOST)


@mcp.tool()
def fusion_execute_python(script: str) -> str:
    """Execute Python inside Fusion 360 with full API access.

    Preloaded globals: `adsk` (adsk.core/adsk.fusion), `app`
    (adsk.core.Application), `ui` (app.userInterface). Use print() for
    output; it is captured and returned. Returns JSON {ok, stdout, error}.

    Example:
        design = app.activeProduct
        root = design.rootComponent
        sk = root.sketches.add(root.xYConstructionPlane)
        print(sk.name)
    """
    try:
        r = httpx.post(
            f"{HOST}/execute",
            headers=_headers(),
            json={"script": script},
            timeout=130,
        )
        return r.text
    except httpx.ConnectError:
        return NOT_RUNNING.format(host=HOST)


@mcp.tool()
def fusion_api_docs(query: str, member: str = "") -> str:
    """Search the Fusion 360 Python API by introspection (offline, exact).

    Finds classes in adsk.core/adsk.fusion/adsk.cam whose name contains
    `query` and lists their members. Pass `member` to filter to matching
    methods/properties with docstrings and signatures.

    Examples:
        fusion_api_docs("ExtrudeFeatures")              -> class + member list
        fusion_api_docs("ExtrudeFeatures", "createInput") -> full docs for member
    """
    try:
        params = {"q": query}
        if member:
            params["member"] = member
        r = httpx.get(f"{HOST}/docs", headers=_headers(), params=params, timeout=30)
        return r.text
    except httpx.ConnectError:
        return NOT_RUNNING.format(host=HOST)


@mcp.tool()
def fusion_screenshot(width: int = 0, height: int = 0) -> Image:
    """Capture a PNG screenshot of the active Fusion 360 viewport.

    width/height of 0 means current viewport size.
    """
    params = {}
    if width:
        params["width"] = width
    if height:
        params["height"] = height
    r = httpx.get(f"{HOST}/screenshot", headers=_headers(), params=params, timeout=60)
    r.raise_for_status()
    return Image(data=r.content, format="png")


def run():
    mcp.run()


if __name__ == "__main__":
    run()
