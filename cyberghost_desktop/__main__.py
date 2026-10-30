import argparse
import sys

from . import __version__


def main():
    parser = argparse.ArgumentParser(description="Desktop controls for the CyberGhost Linux CLI")
    parser.add_argument("--demo", action="store_true", help="preview with sample data; never invokes CyberGhost")
    parser.add_argument("--no-tray", action="store_true", help="close the app when its window is closed")
    parser.add_argument("--setup", action="store_true", help="open the guided CLI installation and account setup")
    parser.add_argument("--check", action="store_true", help="report Linux dependencies without opening the UI or changing the VPN")
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args()
    if sys.version_info < (3, 10):
        print("CyberGhost-Linux-UI needs Python 3.10 or newer with matching system GTK 3 bindings.", file=sys.stderr)
        return 1
    from .linux_support import dependency_help, distribution_name, systemd_available
    if args.check:
        import shutil
        from .setup_tasks import find_terminal
        print(distribution_name())
        print(f"Python: {sys.version.split()[0]} ({sys.executable})")
        print("Boot connection: " + ("systemd available" if systemd_available() else "unavailable (systemd is not running)"))
        for tool in ("bash", "cyberghostvpn", "pkexec", "sudo", "ip", "nft", "wg"):
            print(f"{tool}: {shutil.which(tool) or shutil.which(tool, path='/usr/sbin:/usr/bin:/sbin:/bin') or 'not installed'}")
        terminal = find_terminal()
        print("Setup terminal: " + (terminal[0] if terminal else "not found; manual setup is available"))
        try:
            import gi
            gi.require_version("Gtk", "3.0")
            from gi.repository import Gtk
            print(f"GTK: {Gtk.MAJOR_VERSION}.{Gtk.MINOR_VERSION}")
        except (ImportError, ValueError) as error:
            print(f"GTK 3: unavailable ({error})")
            print(dependency_help())
            return 1
        print("\n" + dependency_help())
        return 0
    try:
        from .app import Application
        from gi.repository import GLib
    except (ImportError, ValueError) as error:
        print("GTK 3 support is missing.\n" + dependency_help(), file=sys.stderr)
        print(error, file=sys.stderr)
        return 1
    GLib.set_prgname("cyberghost-desktop")
    GLib.set_application_name("CyberGhost Desktop")
    return Application(demo=args.demo, tray=not args.no_tray, setup=args.setup).run([sys.argv[0]])


if __name__ == "__main__":
    raise SystemExit(main())
