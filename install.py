#!/usr/bin/python3
"""Install/remove per-user launchers only. Does not install or configure the VPN."""

import argparse
import os
from pathlib import Path
import shlex
import sys
import tempfile

if sys.version_info < (3, 10):
    raise SystemExit("CyberGhost-Linux-UI needs Python 3.10 or newer with matching system GTK 3 bindings.")

from cyberghost_desktop.startup import Autostart  # noqa: E402
from cyberghost_desktop.startup_system import Paths  # noqa: E402

MARKER = "cyberghost-desktop managed launcher"
APP_ID = "io.github.rvage.CyberGhostDesktop"


def targets(home: Path):
    return {
        "command": home / ".local/bin/cyberghost-desktop",
        "desktop": home / f".local/share/applications/{APP_ID}.desktop",
        "icon": home / ".local/share/icons/hicolor/scalable/apps/cyberghost-desktop.svg",
    }


def managed_write(path: Path, text: str, mode: int):
    if path.is_symlink() or (path.exists() and MARKER not in path.read_text()):
        raise RuntimeError(f"Refusing to replace an unrelated file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(text)
            stream.flush()
            os.fchmod(stream.fileno(), mode)
        os.replace(temporary, path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def desktop_arg(value: str) -> str:
    value = value.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$").replace("%", "%%")
    return '"' + value + '"'


def install(home: Path, project: Path):
    paths = targets(home)
    config = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config")) if home == Path.home() else home / ".config"
    autostart = Autostart(config=config, project=project)
    autostart.validate()
    # Validate all destinations before writing any of them.
    for path in paths.values():
        if path.is_symlink() or (path.exists() and MARKER not in path.read_text()):
            raise RuntimeError(f"An unrelated launcher already exists: {path}")
    # ZIP extraction can drop executable bits. Invoke the script's interpreter
    # explicitly; the installed launcher itself is always marked executable.
    launcher = f'#!/bin/sh\n# {MARKER}\nexec /bin/sh {shlex.quote(str(project / "run.sh"))} "$@"\n'
    icon = (project / "assets/cyberghost-desktop.svg").read_text().replace("<svg ", f"<!-- {MARKER} -->\n<svg ", 1)
    desktop = (
        f"[Desktop Entry]\n# {MARKER}\nVersion=1.0\nType=Application\n"
        "Name=CyberGhost Desktop\nComment=Desktop controls for the CyberGhost VPN CLI\n"
        f"Exec={desktop_arg(str(paths['command']))}\nIcon={paths['icon']}\n"
        "Terminal=false\nCategories=Network;\nStartupNotify=true\n"
        "StartupWMClass=cyberghost-desktop\n"
    )
    managed_write(paths["command"], launcher, 0o755)
    managed_write(paths["icon"], icon, 0o644)
    managed_write(paths["desktop"], desktop, 0o644)
    autostart.relocate()
    return paths


def uninstall(home: Path):
    if home == Path.home() and Paths(os.getuid()).profile.exists():
        raise RuntimeError("Disable 'Connect VPN during boot' in the app before uninstalling, so its authorized startup service is removed.")
    config = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config")) if home == Path.home() else home / ".config"
    Autostart(config=config).remove()
    for path in targets(home).values():
        if path.is_file() and not path.is_symlink() and MARKER in path.read_text():
            path.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uninstall", action="store_true")
    args = parser.parse_args()
    if args.uninstall:
        uninstall(Path.home())
        print("Removed this app's user launchers. VPN and preferences unchanged.")
    else:
        paths = install(Path.home(), Path(__file__).resolve().parent)
        print("Installed CyberGhost Desktop in your application menu.")
        print("Launcher:", paths["command"])
        print("Open the app and choose Setup to install the CyberGhost CLI or configure your account if needed.")


if __name__ == "__main__":
    main()
