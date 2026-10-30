"""CLI boundary and read-only tunnel checks. No shells or stored credentials."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import pwd
import re
import shlex
import shutil
import subprocess
from typing import Callable

ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
MODES = {"traffic", "streaming", "torrent"}


class CliError(RuntimeError):
    def __init__(self, message: str, details: str = ""):
        super().__init__(message)
        self.details = details


class NetworkUnavailable(CliError):
    """A read-only API request may be retried when the network becomes ready."""


@dataclass(frozen=True)
class Location:
    name: str
    code: str
    service: str = ""

    @property
    def key(self) -> str:
        return f"{self.code}:{self.service}"


@dataclass(frozen=True)
class Choice:
    value: str
    label: str
    load: int | None = None


@dataclass(frozen=True)
class Status:
    state: str  # connected, disconnected, unknown
    detail: str


def clean_output(text: str) -> str:
    return ANSI.sub("", text).replace("\r", "").strip()


def table_rows(text: str) -> list[dict[str, str]]:
    """Read CyberGhost 1.5's PrettyTable output; ignore banners and separators."""
    headers: list[str] = []
    rows = []
    for line in clean_output(text).splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if not headers:
            headers = [cell.lower() for cell in cells]
            continue
        if len(cells) == len(headers) and cells[0].isdigit():
            rows.append(dict(zip(headers, cells)))
    return rows


def parse_locations(text: str, mode: str) -> list[Location]:
    locations = {}
    for row in table_rows(text):
        code = row.get("country code", "").upper()
        name = row.get("service" if mode == "streaming" else "country name", "")
        if re.fullmatch(r"[A-Z]{2}", code) and name:
            location = Location(name, code, name if mode == "streaming" else "")
            locations[location.key] = location
    return sorted(locations.values(), key=lambda item: (item.name.casefold(), item.code))


def parse_choices(text: str, kind: str) -> list[Choice]:
    choices = {}
    for row in table_rows(text):
        value = row.get("city" if kind == "city" else "instance", "")
        if not value:
            continue
        match = re.fullmatch(r"(\d+)%", row.get("load", ""))
        load = int(match[1]) if match else None
        suffix = f" · {load}% load" if load is not None else ""
        if kind == "city" and row.get("instance", "").isdigit():
            suffix = f" · {row['instance']} servers" + suffix
        choices[value] = Choice(value, value + suffix, load)
    return sorted(choices.values(), key=lambda item: item.value.casefold())


def parse_status(text: str) -> Status:
    text = clean_output(text)
    # Negative phrases must be checked first: 'disconnected' contains 'connected'.
    if re.search(r"no (?:vpn|wireguard) connections? found|not connected|(?:vpn|wireguard).*(?:inactive|disconnected)", text, re.I):
        return Status("disconnected", text)
    if re.search(r"(?:vpn|wireguard) connection (?:found|is active|is connected|active)|^connected to\b", text, re.I | re.M):
        return Status("connected", text)
    return Status("unknown", text or "The CLI did not return a connection status.")


def _value(value: str, label: str) -> str:
    if not isinstance(value, str) or len(value) > 160 or value.startswith("-") or any(ord(c) < 32 for c in value):
        raise ValueError(f"Invalid {label}.")
    return value


@dataclass(frozen=True)
class Selection:
    mode: str = "traffic"
    country: str = ""
    service: str = ""
    city: str = ""
    server: str = ""
    protocol: str = "wireguard"
    transport: str = "UDP"

    def location_args(self) -> list[str]:
        if self.mode not in MODES or not re.fullmatch(r"[A-Z]{2}", self.country):
            raise ValueError("Choose a location first.")
        if self.mode == "streaming":
            if not self.service:
                raise ValueError("Choose a streaming service first.")
            args = ["--streaming", _value(self.service, "streaming service")]
        else:
            args = ["--" + self.mode]
        args += ["--country-code", self.country]
        if self.city:
            args += ["--city", _value(self.city, "city")]
        if self.server:
            if not self.city:
                raise ValueError("Choose a city before a specific server.")
            args += ["--server", _value(self.server, "server")]
        return args

    def connect_args(self) -> list[str]:
        args = self.location_args()
        if self.protocol not in {"openvpn", "wireguard"}:
            raise ValueError("Choose OpenVPN or WireGuard.")
        args += ["--" + self.protocol]
        if self.protocol == "openvpn":
            if self.transport not in {"UDP", "TCP"}:
                raise ValueError("OpenVPN transport must be UDP or TCP.")
            args += ["--connection", self.transport]
        return args + ["--connect"]

    def command_preview(self) -> str:
        return shlex.join(["cyberghostvpn", *self.connect_args()])


class CyberGhostClient:
    demo = False

    def __init__(self, binary: str | None = None, run: Callable = subprocess.run):
        self.binary = binary or shutil.which("cyberghostvpn")
        self.run = run

    def _call(self, args: list[str], *, privileged: bool = False) -> str:
        if not self.binary:
            raise CliError("CyberGhost CLI is not installed.", "Open Setup to install CyberGhost's Linux CLI, then click Check again.")
        command = [self.binary, *args]
        if privileged:
            pkexec = shutil.which("pkexec")
            if not pkexec:
                raise CliError("Linux authorization is unavailable.", "Install polkit and a desktop authentication agent to connect or disconnect.")
            try:
                username = pwd.getpwuid(os.getuid()).pw_name
            except KeyError as error:
                raise CliError("Could not identify your Linux account.",
                               "Open CyberGhost Desktop from your normal desktop user account.") from error
            # CyberGhost uses SUDO_USER to locate the account configured by its
            # sudo-based setup. pkexec clears that variable, so set it *after*
            # authorization using the real UID, never inherited USER/SUDO_USER.
            # Keep pkexec's sanitized environment; only add these fixed keys.
            command = [pkexec, "--disable-internal-agent", "/usr/bin/env",
                       f"SUDO_USER={username}", "LC_ALL=C", "LANG=C", *command]
        try:
            result = self.run(
                command, capture_output=True, text=True, errors="replace",
                stdin=subprocess.DEVNULL,
                # Do not kill a privileged connect midway through network changes.
                # Polkit owns cancellation; the window stays responsive meanwhile.
                timeout=None if privileged else 40,
                env={**os.environ, "LC_ALL": "C", "LANG": "C"},
            )
        except subprocess.TimeoutExpired as error:
            error_type = NetworkUnavailable if not privileged and "--country-code" in args else CliError
            raise error_type("CyberGhost took too long to respond.", "Check your network and try Refresh again.") from error
        except OSError as error:
            raise CliError("Could not start CyberGhost.", str(error)) from error
        output = clean_output(result.stdout + "\n" + result.stderr)
        if privileged and result.returncode in {126, 127}:
            raise CliError("Authorization was cancelled or unavailable.", output)
        # During login the route, DNS and boot VPN can become ready at different
        # times. Classify only known network exceptions; account/configuration
        # failures must remain visible immediately. Some CLI failures exit zero.
        if not privileged:
            if any(marker in output for marker in ("socket.gaierror:", "urllib3.exceptions.NameResolutionError:")):
                raise NetworkUnavailable("CyberGhost's server name could not be resolved. Check your network/DNS and try Refresh.", output)
            if any(marker in output for marker in (
                "requests.exceptions.ConnectionError:", "requests.exceptions.ConnectTimeout:",
                "requests.exceptions.ReadTimeout:", "urllib3.exceptions.NewConnectionError:",
            )):
                raise NetworkUnavailable("CyberGhost's servers could not be reached. Check your network and try Refresh.", output)
        if result.returncode:
            raise CliError("CyberGhost could not complete this request.", output)
        # Some CLI failures have historically exited zero. Never turn them into a
        # successful connection; status is always queried separately after actions.
        return output

    def status(self) -> Status:
        status = parse_status(self._call(["--status"]))
        if status.state == "connected":
            return status
        # CLI 1.5.0 runs `wg show` and treats its permission failure as no VPN.
        # Reading link state and a local route lookup needs no elevation, does not
        # send packets, and works after reopening the UI without cached success.
        wireguard = self._wireguard_status()
        return wireguard if wireguard is not None else status

    def _ip_json(self, ip: str, args: list[str]) -> list[dict]:
        result = self.run([ip, "-j", *args], capture_output=True, text=True, errors="replace",
                          stdin=subprocess.DEVNULL, timeout=10,
                          env={**os.environ, "LC_ALL": "C", "LANG": "C"})
        if result.returncode:
            raise ValueError(clean_output(result.stderr)[:240] or "Linux could not read network state.")
        data = json.loads(result.stdout)
        if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
            raise ValueError("Linux returned an unexpected network status format.")
        return data

    def _wireguard_status(self) -> Status | None:
        ip = shutil.which("ip") or shutil.which("ip", path="/usr/sbin:/sbin:/usr/bin:/bin")
        if not ip:
            return Status("unknown", "Cannot verify WireGuard status: the ip command is missing (iproute2).")
        try:
            links = self._ip_json(ip, ["-details", "link", "show", "type", "wireguard"])
            # The official CLI creates this exact interface. Other VPNs' wg0,
            # tun0, etc. must not be reported as a CyberGhost connection.
            link = next((item for item in links if item.get("ifname") == "cyberghost"), None)
            if link is None:
                return None
            info, flags = link.get("linkinfo"), link.get("flags")
            if not isinstance(info, dict) or info.get("info_kind") != "wireguard" or not isinstance(flags, list):
                raise ValueError("Linux returned incomplete WireGuard interface information.")
            if "UP" not in flags:
                return Status("disconnected", "CyberGhost WireGuard interface is down.")
            # WireGuard normally reports operstate UNKNOWN even when working.
            # Verify routing as well as the administrative UP flag. `route get`
            # only consults the kernel tables; it never contacts this address.
            routes = self._ip_json(ip, ["-4", "route", "get", "1.1.1.1"])
            if any(route.get("dev") == "cyberghost" and route.get("type", "unicast") == "unicast" for route in routes):
                return Status("connected", "CyberGhost WireGuard interface is active and the IPv4 internet route uses it.")
            return Status("unknown", "CyberGhost WireGuard interface is up, but the IPv4 internet route does not use it. "
                          "Check or disconnect the existing tunnel before reconnecting.")
        except subprocess.TimeoutExpired:
            return Status("unknown", "WireGuard status check timed out. Use Refresh to retry.")
        except (OSError, ValueError) as error:
            return Status("unknown", f"Cannot verify WireGuard status: {error}")

    def locations(self, mode: str) -> list[Location]:
        if mode not in MODES:
            raise ValueError("Unknown server category.")
        output = self._call(["--" + mode, "--country-code"])
        result = parse_locations(output, mode)
        if not result:
            raise CliError("No locations were returned.", output or "Check that the CLI is set up and your account is active.")
        return result

    def cities(self, selection: Selection) -> list[Choice]:
        return parse_choices(self._call(selection.location_args() + ["--city"]), "city")

    def servers(self, selection: Selection) -> list[Choice]:
        if not selection.city:
            return []
        return parse_choices(self._call(selection.location_args() + ["--server"]), "server")

    def connect(self, selection: Selection) -> str:
        return self._call(selection.connect_args(), privileged=True)

    def disconnect(self) -> str:
        return self._call(["--stop"], privileged=True)


class DemoClient:
    """Explicit, isolated preview mode. Never invokes CyberGhost or polkit."""

    demo = True
    binary = "demo"

    def __init__(self):
        self.connected = False

    def locations(self, mode: str) -> list[Location]:
        if mode == "streaming":
            return [Location("Example streaming DE", "DE", "Example streaming DE")]
        return [Location(name, code) for name, code in [
            ("Austria", "AT"), ("France", "FR"), ("Germany", "DE"), ("Netherlands", "NL"),
            ("Sweden", "SE"), ("Switzerland", "CH"), ("United Kingdom", "GB"), ("United States", "US"),
        ]]

    def cities(self, selection: Selection) -> list[Choice]:
        return [Choice("Berlin", "Berlin · 110 servers · 47% load", 47), Choice("Frankfurt", "Frankfurt · 993 servers · 48% load", 48)]

    def servers(self, selection: Selection) -> list[Choice]:
        return [Choice("frankfurt-s415-i01", "frankfurt-s415-i01 · 51% load", 51)]

    def status(self) -> Status:
        return Status("connected" if self.connected else "disconnected", "Demo mode: your actual VPN is unchanged.")

    def connect(self, selection: Selection) -> str:
        selection.connect_args()
        self.connected = True
        return "Demo connection completed. No network settings were changed."

    def disconnect(self) -> str:
        self.connected = False
        return "Demo connection closed."
