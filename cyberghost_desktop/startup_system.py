"""One-time authorized installation and fixed-profile boot runner.

The installed runner and its adapters are root-owned copies. The boot service
never imports code from the user's checkout or accepts commands from preferences.
"""

from dataclasses import asdict, dataclass
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import sys
import tempfile
import time

MARKER = "cyberghost-desktop managed startup"
SYSTEM_PATH = "/usr/sbin:/usr/bin:/sbin:/bin"
ENV = {"PATH": SYSTEM_PATH, "LC_ALL": "C", "LANG": "C"}
COPIES = ("startup_system.py", "cli.py", "firewall.py")


@dataclass(frozen=True)
class Paths:
    uid: int
    root: Path = Path("/")

    @property
    def name(self):
        if type(self.uid) is not int or self.uid <= 0:
            raise ValueError("Startup must belong to a normal user account.")
        return f"cyberghost-desktop-startup-{self.uid}.service"

    @property
    def library(self):
        return self.root / f"usr/local/libexec/cyberghost-desktop/startup-{self.uid}"

    @property
    def profile(self):
        return self.root / f"etc/cyberghost-desktop/startup-{self.uid}.json"

    @property
    def unit(self):
        return self.root / "etc/systemd/system" / self.name

    @property
    def runtime(self):
        return self.root / f"run/cyberghost-desktop-startup/{self.uid}"


def adapter(name, directory):
    module_name = "_cyberghost_startup_" + name
    spec = importlib.util.spec_from_file_location(module_name, directory / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def trusted(path):
    """Reject mutable executables, directories and symlink destinations."""
    resolved = path.resolve(strict=True)
    for item in (resolved, *resolved.parents):
        info = item.stat()
        if info.st_uid != 0 or info.st_mode & 0o022:
            raise RuntimeError(f"Startup requires a root-owned path that other users cannot modify: {item}")
    return resolved


def systemctl(*args):
    result = subprocess.run(["/usr/bin/systemctl", *args], stdin=subprocess.DEVNULL,
                            capture_output=True, text=True, env=ENV, check=False, timeout=30)
    if result.returncode:
        raise RuntimeError("Could not update the startup service: " + result.stderr.strip()[:400])
    return result.stdout


def service_text(paths):
    return f'''# {MARKER}
[Unit]
Description=CyberGhost Desktop startup connection for user {paths.uid}
Wants=network-online.target
After=network-online.target

[Service]
Type=exec
ExecStart=/usr/bin/python3 -I /usr/local/libexec/cyberghost-desktop/startup-{paths.uid}/startup_system.py run {paths.uid}
RemainAfterExit=yes
Restart=no
TimeoutStartSec=30
UMask=0077
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
'''


def directory(path):
    path.mkdir(parents=True, exist_ok=True, mode=0o755)
    trusted(path)
    # systemd's UMask=0077 also affects mkdir. These app-owned directories
    # contain public status/configuration (no credentials), which the GUI reads.
    path.chmod(0o755)


def write(path, contents, mode=0o644):
    directory(path.parent)
    if path.is_symlink():
        raise RuntimeError(f"Refusing to replace a symlink: {path}")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            os.fchmod(stream.fileno(), mode)
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def read_profile(path):
    trusted(path)
    if path.stat().st_size > 16384:
        raise RuntimeError("Startup profile is too large.")
    data = json.loads(path.read_text())
    if data.get("managed_by") != MARKER:
        raise RuntimeError("This startup profile belongs to another application.")
    return data


def profile_for(account, payload, source):
    if not isinstance(payload, dict) or set(payload) != {"selection", "vpn_only"}:
        raise ValueError("Invalid startup configuration.")
    if type(payload["vpn_only"]) is not bool or not isinstance(payload["selection"], dict):
        raise ValueError("Invalid startup options.")
    cli = adapter("cli", source)
    selection = cli.Selection(**payload["selection"])
    selection.connect_args()
    if selection.protocol != "wireguard" and payload["vpn_only"]:
        raise ValueError("VPN only at startup requires WireGuard.")
    required = ["ip", "systemctl"] + (["nft", "wg"] if payload["vpn_only"] else [])
    for tool in required:
        if not shutil.which(tool, path=SYSTEM_PATH):
            raise RuntimeError(f"Install {tool} before enabling these startup options.")
    binary = shutil.which("cyberghostvpn", path=SYSTEM_PATH)
    if not binary:
        raise RuntimeError("Install and configure the CyberGhost CLI before enabling startup.")
    binary = trusted(Path(binary))
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise RuntimeError("CyberGhost CLI is not executable.")
    # The official CLI uses this path based on SUDO_USER. Do not read credentials
    # or change their ownership; the same configured account is used at boot.
    config = Path("/home") / account.pw_name / ".cyberghost/config.ini"
    if not config.is_file():
        raise RuntimeError("Configure this user's CyberGhost account in Setup before enabling startup.")
    return {"managed_by": MARKER, "uid": account.pw_uid, "username": account.pw_name,
            "binary": str(binary), "selection": asdict(selection), "vpn_only": payload["vpn_only"]}


def install_profile(account, payload, *, paths=None, source=None, control=systemctl):
    paths = paths or Paths(account.pw_uid)
    source = source or Path(__file__).resolve().parent
    profile = profile_for(account, payload, source)
    for existing in paths.profile.parent.glob("startup-*.json"):
        if existing != paths.profile:
            read_profile(existing)
            raise RuntimeError("Startup is already configured for another account. Disable that account's startup option first.")
    for item in (paths.unit, paths.profile):
        if item.is_symlink() or (item.exists() and MARKER not in item.read_text()):
            raise RuntimeError(f"Refusing to replace another application's file: {item}")
    if paths.library.exists():
        trusted(paths.library)
        if not (paths.library / ".owner").is_file() or (paths.library / ".owner").read_text() != MARKER:
            raise RuntimeError("The startup helper directory is not managed by this application.")
    main_pid = control("show", paths.name, "--property=MainPID", "--value").strip()
    job = control("show", paths.name, "--property=Job", "--value").strip()
    if (main_pid and main_pid != "0") or (job and job != "0"):
        raise RuntimeError("Wait for the current boot connection to finish before changing startup.")
    old = {}
    targets = [paths.unit, paths.profile, paths.library / ".owner", *(paths.library / name for name in COPIES)]
    for item in targets:
        if item.exists():
            trusted(item)
        old[item] = item.read_text() if item.exists() else None
    try:
        for name in COPIES:
            write(paths.library / name, (source / name).read_text())
        write(paths.library / ".owner", MARKER)
        write(paths.profile, json.dumps(profile, indent=2) + "\n")
        write(paths.unit, service_text(paths))
        control("daemon-reload")
        # Deliberately no --now: enabling this option must not reconnect the
        # user's live session. It takes effect on the next boot.
        control("enable", paths.name)
    except Exception:
        for item, contents in old.items():
            if contents is None:
                item.unlink(missing_ok=True)
            else:
                write(item, contents)
        try:
            if old[paths.unit] is None:
                control("disable", paths.name)
            control("daemon-reload")
        except Exception:
            pass  # Retain the original setup error after restoring the files.
        raise
    return profile


def remove_profile(paths, *, control=systemctl):
    if not paths.profile.exists():
        return
    profile = read_profile(paths.profile)
    if profile.get("uid") != paths.uid:
        raise RuntimeError("The startup profile does not match this user.")
    main_pid = control("show", paths.name, "--property=MainPID", "--value").strip()
    job = control("show", paths.name, "--property=Job", "--value").strip()
    if (main_pid and main_pid != "0") or (job and job != "0"):
        raise RuntimeError("Wait for the current boot connection to finish before disabling startup.")
    for item in (paths.unit, paths.library / ".owner"):
        trusted(item)
        if MARKER not in item.read_text():
            raise RuntimeError("Startup files are not owned by this application.")
    # Do not stop the service: an already established OpenVPN process or the
    # firewall monitor may be in its cgroup. The current VPN stays unchanged.
    control("disable", paths.name)
    paths.unit.unlink()
    paths.profile.unlink()
    for name in (*COPIES, ".owner"):
        item = paths.library / name
        if item.is_symlink():
            raise RuntimeError("Unexpected symlink in the startup helper directory.")
        item.unlink(missing_ok=True)
    paths.library.rmdir()
    control("daemon-reload")


def outcome(paths, state, detail):
    write(paths.runtime / "status.json", json.dumps({"state": state, "detail": detail}) + "\n")
    print(detail, flush=True)


def boot_connect(paths, *, run=subprocess.run, sleep=time.sleep, now=time.monotonic):
    profile = read_profile(paths.profile)
    account = pwd.getpwuid(paths.uid)
    if profile.get("uid") != paths.uid or profile.get("username") != account.pw_name:
        raise RuntimeError("The saved startup account no longer matches this user.")
    if not (Path("/home") / account.pw_name / ".cyberghost/config.ini").is_file():
        raise RuntimeError("The CyberGhost account file is unavailable during boot. An encrypted or login-mounted home may require connecting after login.")
    binary = str(trusted(Path(profile["binary"])))
    for name in COPIES:
        trusted(paths.library / name)
    cli = adapter("cli", paths.library)
    selection = cli.Selection(**profile["selection"])
    args = selection.connect_args()
    env = {**ENV, "SUDO_USER": account.pw_name, "HOME": account.pw_dir, "USER": "root", "LOGNAME": "root"}

    def invoke(command, **kwargs):
        kwargs["env"] = env
        return run(command, **kwargs)

    client = cli.CyberGhostClient(binary, run=invoke)
    firewall = adapter("firewall", paths.library)
    status = client.status()
    if status.state == "unknown":
        raise RuntimeError("Could not verify the existing VPN. Startup did not replace it.")
    if status.state != "connected":
        # A persisted/externally installed guard must never be silently removed.
        if shutil.which("nft", path=SYSTEM_PATH) and firewall.Firewall(run=invoke).document() is not None:
            raise RuntimeError("VPN-only rules are already active. Startup will not disable them to connect.")
        outcome(paths, "waiting", "Startup VPN is waiting for the network…")
        ip = shutil.which("ip", path=SYSTEM_PATH)
        if not ip:
            raise RuntimeError("Install iproute2 for startup network checks.")
        deadline = now() + 180
        while True:
            try:
                routes = client._ip_json(ip, ["-4", "route", "get", "1.1.1.1"])
            except (OSError, ValueError, subprocess.TimeoutExpired):
                routes = []
            if any(route.get("dev") not in {None, "lo"} and route.get("type", "unicast") == "unicast" for route in routes):
                break
            if now() >= deadline:
                raise RuntimeError("No network became available within three minutes. Connect manually from the app.")
            sleep(3)
        # Someone may have connected while Wi-Fi was becoming available.
        status = client.status()
        if status.state == "unknown":
            raise RuntimeError("VPN status changed during startup; no automatic connection was attempted.")
        if status.state == "disconnected":
            outcome(paths, "connecting", "Startup VPN is connecting to the saved location…")
            result = invoke([binary, *args], capture_output=True, text=True, errors="replace",
                            stdin=subprocess.DEVNULL, timeout=None, check=False)
            status = client.status()
            if status.state == "connected" and selection.protocol == "wireguard":
                routed = client._wireguard_status()
                if routed is None or routed.state != "connected":
                    raise RuntimeError("CyberGhost reported a connection, but its WireGuard internet route could not be verified.")
            if status.state != "connected":
                raise RuntimeError(f"Startup VPN could not verify a connection (CLI exit {result.returncode}). Connect manually from the app.")
    if profile.get("vpn_only"):
        if selection.protocol != "wireguard":
            raise RuntimeError("Saved VPN-only startup settings require WireGuard.")
        outcome(paths, "protecting", "Startup VPN connected; enabling VPN-only protection…")
        protection = firewall.authorized_action("enable")
        if protection.state != "on":
            raise RuntimeError("The VPN connected, but VPN-only protection could not be verified.")
    outcome(paths, "connected", "Startup VPN connected." + (" VPN-only protection is enabled." if profile.get("vpn_only") else ""))


def main():
    try:
        if os.geteuid() != 0 or len(sys.argv) < 2:
            raise RuntimeError("Startup setup requires administrator authorization.")
        action = sys.argv[1]
        if action == "run" and len(sys.argv) == 3:
            paths = Paths(int(sys.argv[2]))
            if Path(__file__).resolve() != paths.library / "startup_system.py":
                raise RuntimeError("Boot execution must use the installed, root-owned helper.")
            directory(paths.runtime.parent)
            directory(paths.runtime)
            descriptor = os.open(paths.runtime / "run.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            with os.fdopen(descriptor, "w") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                try:
                    boot_connect(paths)
                except Exception as error:
                    outcome(paths, "error", str(error))
                    return 1
            return 0
        uid = int(os.environ.get("PKEXEC_UID", "0"))
        if uid <= 0:
            raise RuntimeError("Enable or disable startup from your normal desktop account.")
        account = pwd.getpwuid(uid)
        if action in {"configure", "remove"}:
            runtime = Path("/run/cyberghost-desktop-startup")
            directory(runtime)
            descriptor = os.open(runtime / "configure.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            with os.fdopen(descriptor, "w") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                if action == "configure" and len(sys.argv) == 3 and len(sys.argv[2]) <= 16384:
                    profile = install_profile(account, json.loads(sys.argv[2]))
                    print(json.dumps(profile))
                    return 0
                if action == "remove" and len(sys.argv) == 2:
                    remove_profile(Paths(uid))
                    print(json.dumps({"removed": True}))
                    return 0
        raise RuntimeError("Expected configure, remove or a fixed boot action.")
    except Exception as error:
        print(json.dumps({"error": str(error)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
