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


def install_addin():
    src = importlib.resources.files("fusion360_bridge") / "addin" / "FusionBridge"
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
        from .server import run

        run()


if __name__ == "__main__":
    main()
