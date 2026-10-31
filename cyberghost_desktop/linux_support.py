"""Read-only Linux capability checks and package guidance; never installs tools."""

import platform
from pathlib import Path
import shutil


def os_release():
    try:
        return platform.freedesktop_os_release()
    except OSError:
        return {}


def distribution_name():
    info = os_release()
    return info.get("PRETTY_NAME") or info.get("NAME") or "Linux (distribution unknown)"


def package_manager():
    info = os_release()
    families = {
        "debian": ("apt-get",), "ubuntu": ("apt-get",), "linuxmint": ("apt-get",),
        "fedora": ("dnf", "yum"), "rhel": ("dnf", "yum"), "centos": ("dnf", "yum"),
        "almalinux": ("dnf", "yum"), "rocky": ("dnf", "yum"),
        "arch": ("pacman",), "manjaro": ("pacman",),
        "opensuse": ("zypper",), "opensuse-tumbleweed": ("zypper",),
        "opensuse-leap": ("zypper",), "suse": ("zypper",), "sles": ("zypper",),
    }
    # Prefer the distribution's own manager even if another happens to be
    # installed. os-release is parsed as data by Python, never sourced as shell.
    for family in (info.get("ID", ""), *info.get("ID_LIKE", "").split()):
        if family in families:
            return next((name for name in families[family] if shutil.which(name)), families[family][0])
    return next((name for name in ("apt-get", "dnf", "yum", "pacman", "zypper") if shutil.which(name)), None)


def dependency_help(format_text=None):
    if format_text is None:
        def format_text(source, **values):
            return source.format(**values)
    manager = package_manager()
    packages = {
        "apt-get": ("python3-gi python3-gi-cairo gir1.2-gtk-3.0 pkexec iproute2 bash sudo", "gir1.2-ayatanaappindicator3-0.1"),
        "dnf": ("python3-gobject gtk3 polkit iproute bash sudo", "libayatana-appindicator-gtk3"),
        "yum": ("python3-gobject gtk3 polkit iproute bash sudo", "libayatana-appindicator-gtk3"),
        "pacman": ("python-gobject gtk3 polkit iproute2 bash sudo", "libayatana-appindicator"),
        "zypper": ("python3-gobject python3-gobject-Gdk typelib-1_0-Gtk-3_0 polkit iproute2 bash sudo", None),
    }
    if manager not in packages:
        return format_text("Install Python 3.10+ with PyGObject/GTK 3, pkexec/polkit, iproute2, Bash and sudo using your "
                "distribution's package manager. Optional: AppIndicator, nftables and wireguard-tools.")
    install = "sudo pacman -S --needed" if manager == "pacman" else f"sudo {manager} install"
    core, tray = packages[manager]
    tray_command = f"{install} {tray}" if tray else format_text("zypper search -s '*appindicator*'\nInstall the GTK 3 AppIndicator typelib offered by your release.")
    return format_text("UI and setup tools:\n{core_command}\n\n"
            "Optional tray:\n{tray_command}\n\n"
            "Optional VPN-only protection:\n{guard_command}\n\n"
            "Use Python 3.10+ and matching system GTK bindings. Package availability depends on your release. "
            "Your desktop also needs a running polkit authentication agent.",
            core_command=f"{install} {core}", tray_command=tray_command,
            guard_command=f"{install} nftables wireguard-tools")


def systemd_available():
    # A systemctl binary alone is insufficient on OpenRC/runit systems or in
    # containers where systemd is installed but does not manage the host.
    return Path("/run/systemd/system").is_dir() and bool(shutil.which("systemctl", path="/usr/sbin:/usr/bin:/sbin:/bin"))
