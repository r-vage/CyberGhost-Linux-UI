"""Fixed-action, polkit-authorized WireGuard firewall helper (stdlib only).

Run as an isolated script, never import project or user modules while privileged.
The optional root monitor exposes read-only status to normal users. Firewall
changes require a root peer, reached through a fresh polkit authorization.
"""

from dataclasses import asdict, dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import stat
import struct
import subprocess
import sys

RUNTIME = Path("/run/cyberghost-desktop")
TABLE = "cyberghost_desktop"
OWNER = "CyberGhost Desktop VPN-only"
INTERFACE = "cyberghost"
SYSTEM_PATH = "/usr/sbin:/usr/bin:/sbin:/bin"


@dataclass(frozen=True)
class GuardStatus:
    state: str  # off, on, unknown
    detail: str


def ruleset(mark):
    if type(mark) is not int or not 0 < mark <= 0xFFFFFFFF:
        raise ValueError("WireGuard must have a nonzero firewall mark.")
    # Interface names survive removal/recreation. Only WireGuard's marked UDP
    # transport may leave another interface, plus local network maintenance.
    # In particular, existing direct connections are NOT exempted.
    return f'''table inet {TABLE} {{
 comment "{OWNER}"
 chain output {{
  type filter hook output priority 20; policy drop;
  oifname "lo" accept
  oifname "{INTERFACE}" accept
  meta l4proto udp meta mark {mark} accept
  udp sport 68 udp dport 67 accept
  ip6 daddr ff02::1:2 udp sport 546 udp dport 547 accept
  ip6 daddr {{ fe80::/10, ff02::/16 }} icmpv6 type {{ nd-router-solicit, nd-neighbor-solicit, nd-neighbor-advert }} accept
 }}
 chain forward {{
  type filter hook forward priority 20; policy drop;
  oifname "{INTERFACE}" accept
  iifname "{INTERFACE}" ct state established,related accept
 }}
}}
'''


def fingerprint(document):
    def clean(value):
        if isinstance(value, dict):
            return {key: clean(item) for key, item in value.items() if key != "handle"}
        if isinstance(value, list):
            return [clean(item) for item in value]
        return value
    entries = [clean(item) for item in document["nftables"] if "metainfo" not in item]
    return hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest()


class Firewall:
    def __init__(self, run=subprocess.run):
        self.run = run
        self.expected = None

    def command(self, tool, *args, source=None):
        binary = shutil.which(tool, path=SYSTEM_PATH)
        if not binary:
            raise RuntimeError(f"Install {tool} to use the WireGuard kill switch.")
        result = self.run([binary, *args], input=source, capture_output=True, text=True, timeout=8,
                          env={"PATH": SYSTEM_PATH, "LC_ALL": "C", "LANG": "C"}, check=False)
        if result.returncode:
            # Never expose wg peer data, private config, or unrelated firewall rules.
            raise RuntimeError(f"Could not complete the {tool} firewall check (exit {result.returncode}).")
        return result.stdout

    def document(self):
        tables = json.loads(self.command("nft", "--json", "list", "tables"))["nftables"]
        if not any(item.get("table", {}).get("name") == TABLE and
                   item["table"].get("family") == "inet" for item in tables):
            return None
        data = json.loads(self.command("nft", "--json", "list", "table", "inet", TABLE))
        table = next(item["table"] for item in data["nftables"] if "table" in item)
        if table.get("comment") != OWNER:
            raise RuntimeError("A different firewall owns this table name; it was left unchanged.")
        return data

    def wireguard_mark(self):
        links = json.loads(self.command("ip", "-j", "-details", "link", "show", "type", "wireguard"))
        if not any(link.get("ifname") == INTERFACE and "UP" in link.get("flags", []) and
                   link.get("linkinfo", {}).get("info_kind") == "wireguard" for link in links):
            raise RuntimeError("Connect CyberGhost with WireGuard before enabling VPN only.")
        routes = json.loads(self.command("ip", "-j", "-4", "route", "get", "1.1.1.1"))
        if not routes or routes[0].get("dev") != INTERFACE or routes[0].get("type", "unicast") != "unicast":
            raise RuntimeError("CyberGhost must be the active IPv4 internet route before enabling VPN only.")
        value = self.command("wg", "show", INTERFACE, "fwmark").strip()
        try:
            mark = int(value, 0)
            ruleset(mark)
        except ValueError:
            raise RuntimeError("CyberGhost WireGuard has no usable firewall mark.") from None
        return mark

    def enable(self, mark):
        existing = self.document()
        source = (f"delete table inet {TABLE}\n" if existing is not None else "") + ruleset(mark)
        # A single nft transaction either installs all rules or leaves the old
        # rules in place. Never flush the system or wg-quick's own tables.
        self.command("nft", "--file", "-", source=source)
        document = self.document()
        if document is None:
            raise RuntimeError("The firewall rules could not be verified.")
        self.expected = fingerprint(document)
        return self.status()

    def disable(self):
        if self.document() is not None:
            self.command("nft", "delete", "table", "inet", TABLE)
        self.expected = None
        return GuardStatus("off", "Off · direct internet access is allowed.")

    def status(self):
        document = self.document()
        if document is None:
            return GuardStatus("off", "Off · direct internet access is allowed.")
        if self.expected is None or fingerprint(document) != self.expected:
            return GuardStatus("unknown", "Firewall rules changed. Protection cannot be verified; turn VPN only off and on again.")
        return GuardStatus("on", "On · direct internet and LAN traffic are blocked outside CyberGhost.")


def peer_uid(connection):
    return struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))[1]


def request(action, runtime=RUNTIME):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(30)
        connection.connect(str(runtime / "firewall.sock"))
        if peer_uid(connection) != 0:
            raise RuntimeError("The firewall status service is not owned by root.")
        connection.sendall(action.encode("ascii") + b"\n")
        response = connection.makefile("rb").readline(4097)
    if len(response) > 4096 or not response.endswith(b"\n"):
        raise RuntimeError("Incomplete firewall status response.")
    data = json.loads(response)
    if "error" in data:
        raise RuntimeError(data["error"])
    if data.get("state") not in {"on", "off", "unknown"} or not isinstance(data.get("detail"), str):
        raise RuntimeError("Invalid firewall status response.")
    return GuardStatus(**data)


def secure_runtime(runtime):
    runtime.mkdir(mode=0o755, exist_ok=True)
    info = runtime.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise RuntimeError("The firewall runtime directory must be owned by root and not writable by other users.")
    # The boot service uses UMask=0077. Normal users still need to reach the
    # status socket; mutations remain protected by peer credentials.
    runtime.chmod(0o755)


def marker(runtime, enabled):
    path = runtime / "active"
    if enabled:
        descriptor = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            stream.write("Firewall may be active; query the root monitor to verify.\n")
    else:
        path.unlink(missing_ok=True)


def serve(listener, firewall, runtime):
    """Read-only requests need no prompt; mutations require a root peer."""
    while True:
        connection, _ = listener.accept()
        stop = False
        with connection:
            connection.settimeout(3)
            try:
                action = connection.makefile("rb").readline(65)
                if action == b"status\n":
                    result = firewall.status()
                elif action == b"disable\n" and peer_uid(connection) == 0:
                    result = firewall.disable()
                    marker(runtime, False)
                    listener.close()
                    (runtime / "firewall.sock").unlink(missing_ok=True)
                    stop = True
                elif action == b"enable\n" and peer_uid(connection) == 0:
                    mark = firewall.wireguard_mark()
                    marker(runtime, True)
                    result = firewall.enable(mark)
                else:
                    raise RuntimeError("This action requires administrator authorization.")
                payload = asdict(result)
            except Exception as error:
                payload = {"error": str(error)}
            try:
                connection.sendall(json.dumps(payload).encode() + b"\n")
            except OSError:
                pass  # A GUI closing cannot disable protection or stop the monitor.
        if stop:
            return


def authorized_action(action, runtime=RUNTIME, firewall=None, *, _lock=None):
    if os.geteuid() != 0 or action not in {"enable", "disable"}:
        raise RuntimeError("Run a fixed firewall action through administrator authorization.")
    secure_runtime(runtime)
    lock = _lock if _lock is not None else os.open(runtime / "action.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        # Serialize independently launched authorization helpers, including two
        # desktops/users acting at once. The monitor does not inherit this lock.
        if _lock is None:
            fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            return request(action, runtime)
        except (FileNotFoundError, ConnectionRefusedError):
            pass
        firewall = firewall or Firewall()
        socket_path = runtime / "firewall.sock"
        socket_path.unlink(missing_ok=True)
        if action == "disable":
            result = firewall.disable()
            marker(runtime, False)
            return result
        mark = firewall.wireguard_mark()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(socket_path))
            os.chmod(socket_path, 0o666)
            listener.listen(8)
            # Write before mutation: if the helper dies during activation the
            # app must report unknown, never assume protection is off.
            marker(runtime, True)
            try:
                result = firewall.enable(mark)
                pid = os.fork()
            except Exception:
                socket_path.unlink(missing_ok=True)
                raise
            if pid == 0:
                os.close(lock)
                os.setsid()
                os.chdir("/")
                null = os.open(os.devnull, os.O_RDWR)
                for descriptor in (0, 1, 2):
                    os.dup2(null, descriptor)
                if null > 2:
                    os.close(null)
                try:
                    serve(listener, firewall, runtime)
                finally:
                    os._exit(0)
            return result
    finally:
        if _lock is None:
            os.close(lock)


def main():
    try:
        if len(sys.argv) != 2:
            raise RuntimeError("Expected enable or disable.")
        result = authorized_action(sys.argv[1])
    except Exception as error:
        print(json.dumps({"error": str(error)}))
        return 1
    print(json.dumps(asdict(result)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
