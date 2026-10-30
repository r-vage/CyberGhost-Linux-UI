"""Desktop startup settings; privileged changes happen only on explicit actions."""

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from .cli import CliError, Selection
from .linux_support import systemd_available
from .startup_system import MARKER, Paths, SYSTEM_PATH, read_profile

DESKTOP_NAME = "io.github.rvage.CyberGhostDesktop.autostart.desktop"


@dataclass(frozen=True)
class StartupState:
    enabled: bool = False
    selection: Selection | None = None
    vpn_only: bool = False
    running: bool = False
    detail: str = "Off · choose a location, then enable connection during boot."
    available: bool = True


def desktop_arg(value):
    if any(ord(char) < 32 for char in value):
        raise ValueError("The project path contains unsupported control characters.")
    value = value.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$").replace("%", "%%")
    return '"' + value + '"'


class Autostart:
    def __init__(self, config=None, project=None):
        config = config or Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        if not Path(config).is_absolute():
            raise ValueError("The desktop config directory must be an absolute path.")
        self.path = Path(config) / "autostart" / DESKTOP_NAME
        self.project = project or Path(__file__).resolve().parents[1]

    def validate(self):
        if self.path.is_symlink() or (self.path.exists() and MARKER not in self.path.read_text()):
            raise RuntimeError("Refusing to replace another application's startup entry.")

    def stage(self):
        self.validate()
        script = self.project / "run.sh"
        if not script.is_file():
            raise RuntimeError("The project launcher is missing. Keep the checkout in place before enabling startup.")
        contents = (f"[Desktop Entry]\n# {MARKER}\nType=Application\nName=CyberGhost Desktop\n"
                    "Comment=Show CyberGhost Desktop after signing in\n"
                    f"Exec=/bin/sh {desktop_arg(str(script))}\n"
                    "Terminal=false\nIcon=network-vpn\nStartupNotify=false\nX-GNOME-Autostart-enabled=true\n")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.NamedTemporaryFile(mode="w", dir=self.path.parent, prefix=".cyberghost-startup-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        return temporary

    def commit(self, temporary):
        self.validate()
        os.replace(temporary, self.path)

    def remove(self):
        self.validate()
        self.path.unlink(missing_ok=True)

    def relocate(self):
        if self.path.exists():
            temporary = self.stage()
            try:
                self.commit(temporary)
            finally:
                temporary.unlink(missing_ok=True)


class StartupManager:
    demo = False

    def __init__(self, *, paths=None, autostart=None, run=subprocess.run):
        self.paths = paths or Paths(os.getuid())
        self.autostart = autostart or Autostart()
        self.run = run

    def inspect(self):
        if not self.paths.profile.exists():
            if self.autostart.path.exists():
                return StartupState(True, detail="Desktop startup exists, but its VPN profile is missing. Save settings again or disable startup.")
            if not systemd_available():
                return StartupState(available=False, detail="Boot connection requires a running systemd system. Manual VPN controls remain available.")
            return StartupState()
        profile = read_profile(self.paths.profile)
        selection = Selection(**profile["selection"])
        selection.connect_args()
        result = self.run(["/usr/bin/systemctl", "show", self.paths.name,
                           "--property=MainPID,ActiveState,UnitFileState,Result,Job"], capture_output=True,
                          text=True, stdin=subprocess.DEVNULL, timeout=10, check=False)
        if result.returncode:
            raise CliError("Could not check the startup VPN service. Refresh to retry.")
        fields = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        if (not fields.get("MainPID", "").isdigit() or "Job" not in fields or
                (fields["Job"] and not fields["Job"].isdigit()) or "ActiveState" not in fields or "UnitFileState" not in fields):
            raise CliError("The startup service returned an incomplete status. Refresh to retry.")
        running = fields["MainPID"] != "0" or fields["Job"] not in {"", "0"} or fields["ActiveState"] in {"activating", "deactivating"}
        target = selection.country + (" · " + selection.service if selection.service else "")
        target += " · " + ("WireGuard" if selection.protocol == "wireguard" else "OpenVPN " + selection.transport)
        if selection.city:
            target += " · " + selection.city
        if selection.server:
            target += " · " + selection.server
        detail = "Saved: " + target + ". Starts on the next boot."
        if fields.get("UnitFileState") != "enabled":
            detail = "Boot service is not enabled. Save the startup settings again."
        elif not self.autostart.path.exists():
            detail = "Boot VPN is enabled, but desktop startup is missing. Save settings to restore it."
        status_file = self.paths.runtime / "status.json"
        if status_file.is_file() and status_file.stat().st_size < 16384:
            try:
                status = json.loads(status_file.read_text())
                if isinstance(status.get("detail"), str):
                    detail = ("" if running else "Last boot: ") + status["detail"][:400] + "\nSaved: " + target
            except (ValueError, OSError):
                pass
        elif running:
            detail = "The boot VPN service is starting…\nSaved: " + target
        elif fields.get("ActiveState") == "failed":
            detail = "The boot VPN service failed. Connect manually and check its system journal.\nSaved: " + target
        return StartupState(True, selection, bool(profile["vpn_only"]), running, detail)

    def _authorize(self, action, payload=None):
        pkexec = shutil.which("pkexec", path=SYSTEM_PATH)
        if not pkexec:
            raise CliError("Install polkit (pkexec) to configure startup.")
        command = [pkexec, "--disable-internal-agent", "/usr/bin/python3", "-I",
                   str(Path(__file__).with_name("startup_system.py").resolve()), action]
        if payload is not None:
            command.append(json.dumps(payload))
        result = self.run(command, capture_output=True, text=True, stdin=subprocess.DEVNULL, check=False)
        if result.returncode in {126, 127}:
            raise CliError("Startup authorization was cancelled or denied.")
        try:
            data = json.loads(result.stdout)
        except ValueError:
            raise CliError("Could not update startup. Check that systemd and polkit are available.") from None
        if result.returncode or "error" in data:
            raise CliError(data.get("error", "Could not update startup."))
        return data

    def configure(self, selection, vpn_only):
        if not systemd_available():
            raise CliError("Boot connection requires a running systemd system. Use the manual VPN controls on this system.")
        selection.connect_args()
        if vpn_only and selection.protocol != "wireguard":
            raise CliError("VPN only at boot requires WireGuard.")
        # Prepare the per-user entry first, without activating it. Cancelling
        # authorization leaves the existing entry and service unchanged.
        temporary = self.autostart.stage()
        try:
            self._authorize("configure", {"selection": asdict(selection), "vpn_only": vpn_only})
            self.autostart.commit(temporary)
        finally:
            temporary.unlink(missing_ok=True)
        return self.inspect()

    def disable(self):
        self.autostart.validate()
        if self.paths.profile.exists():
            self._authorize("remove")
        self.autostart.remove()
        return self.inspect()


class DemoStartup:
    demo = True

    def __init__(self):
        self.state = StartupState()

    def inspect(self):
        return self.state

    def configure(self, selection, vpn_only):
        selection.connect_args()
        self.state = StartupState(True, selection, vpn_only, False, "Demo · saved startup location: " + selection.country)
        return self.state

    def disable(self):
        self.state = StartupState()
        return self.state
