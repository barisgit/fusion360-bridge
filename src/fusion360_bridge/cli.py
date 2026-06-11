"""fusion360-bridge CLI.

Commands:
    serve          Run the stdio MCP server (default when no command given).
    install-addin  Copy the FusionBridge add-in into Fusion 360's AddIns dir.
    health         Quick connectivity check against the running add-in.
"""

import argparse
import importlib.resources
import os
import platform
import shutil
import sys


def _addin_source():
    """Locate the bundled add-in: wheel package data, or repo root in dev."""
    pkg = importlib.resources.files("fusion360_bridge") / "addin" / "FusionBridge"
    if pkg.is_dir():
        return pkg
    repo = os.path.normpath(
        os.path.join(os.path.dirname(__file__), "..", "..", "addin", "FusionBridge")
    )
    if os.path.isdir(repo):
        return repo
    return None


def _addins_dir():
    system = platform.system()
    if system == "Darwin":
        return os.path.expanduser(
            "~/Library/Application Support/Autodesk/Autodesk Fusion 360/API/AddIns"
        )
    if system == "Windows":
        return os.path.expandvars(
            r"%APPDATA%\Autodesk\Autodesk Fusion 360\API\AddIns"
        )
    sys.exit("Fusion 360 only runs on macOS and Windows.")


def ensure_addin_installed():
    """Install the add-in if absent. Quiet no-op when present or symlinked.

    Called on `serve` startup so a fresh machine needs no separate install
    step: the manifest has runOnStartup, so the next Fusion launch loads it.
    """
    try:
        dest = os.path.join(_addins_dir(), "FusionBridge")
    except SystemExit:
        return
    if os.path.islink(dest) or os.path.exists(dest):
        return
    src = _addin_source()
    if src is None:
        return
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if isinstance(src, str):
        shutil.copytree(src, dest)
    else:
        with importlib.resources.as_file(src) as src_path:
            shutil.copytree(src_path, dest)
    print(
        f"FusionBridge add-in installed to {dest}. "
        "Restart Fusion 360 (or enable via Shift+S -> Add-Ins -> Run) to activate.",
        file=sys.stderr,
    )


def install_addin():
    src = _addin_source()
    if src is None:
        sys.exit("Bundled add-in not found in this installation.")
    dest_root = _addins_dir()
    dest = os.path.join(dest_root, "FusionBridge")
    os.makedirs(dest_root, exist_ok=True)
    if os.path.islink(dest):
        sys.exit(
            f"{dest} is a symlink (dev setup?). Remove it first if you want "
            "a copied install."
        )
    if os.path.exists(dest):
        shutil.rmtree(dest)
    if isinstance(src, str):
        shutil.copytree(src, dest)
    else:
        with importlib.resources.as_file(src) as src_path:
            shutil.copytree(src_path, dest)
    print(f"Installed add-in to: {dest}")
    print(
        "Now in Fusion 360: Shift+S -> Add-Ins tab -> FusionBridge -> Run "
        '(check "Run on Startup").'
    )


def health():
    import httpx

    from .server import HOST, SECRET_PATH

    try:
        with open(SECRET_PATH) as f:
            token = f.read().strip()
    except FileNotFoundError:
        sys.exit(
            f"No secret at {SECRET_PATH}. Start Fusion with the FusionBridge "
            "add-in running once to generate it."
        )
    try:
        r = httpx.get(
            f"{HOST}/health",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        print(r.text)
    except httpx.ConnectError:
        sys.exit(
            f"Could not reach {HOST}. Is Fusion 360 running with the "
            "FusionBridge add-in enabled?"
        )


def main():
    parser = argparse.ArgumentParser(prog="fusion360-bridge")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("serve", help="run the stdio MCP server (default)")
    sub.add_parser("install-addin", help="install the Fusion 360 add-in")
    sub.add_parser("health", help="check connectivity to the running add-in")
    args = parser.parse_args()

    if args.command == "install-addin":
        install_addin()
    elif args.command == "health":
        health()
    else:
        ensure_addin_installed()
        from .server import run

        run()


if __name__ == "__main__":
    main()
