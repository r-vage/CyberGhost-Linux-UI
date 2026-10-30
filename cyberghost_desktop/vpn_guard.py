"""Unprivileged kill-switch client. Opening the app never changes firewall rules."""

import json
from pathlib import Path
import shutil
import subprocess

from .cli import CliError
from .firewall import GuardStatus, RUNTIME, SYSTEM_PATH, request


class VPNGuard:
    demo = False

    def status(self):
        try:
            return request("status")
        except (FileNotFoundError, ConnectionRefusedError):
            if (RUNTIME / "active").exists():
                return GuardStatus("unknown", "Firewall monitor unavailable. Rules may still block traffic; turn VPN only off to recover.")
            return GuardStatus("off", "Off · enable after connecting with WireGuard.")
        except Exception:
            return GuardStatus("unknown", "Could not verify firewall protection. Refresh or turn VPN only off to recover.")

    def change(self, enabled):
        pkexec = shutil.which("pkexec", path=SYSTEM_PATH)
        if not pkexec:
            raise CliError("Install polkit (pkexec) to change VPN-only protection.")
        helper = str(Path(__file__).with_name("firewall.py").resolve())
        result = subprocess.run([pkexec, "--disable-internal-agent", "/usr/bin/python3", "-I", helper,
                                 "enable" if enabled else "disable"], stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, check=False)
        if result.returncode in {126, 127}:
            raise CliError("Firewall authorization was cancelled or denied.")
        try:
            data = json.loads(result.stdout)
        except ValueError:
            raise CliError("Could not change VPN-only protection. Check that polkit and nftables are installed.") from None
        if result.returncode or "error" in data:
            raise CliError(data.get("error", "Could not change VPN-only protection."))
        if data.get("state") not in {"on", "off", "unknown"}:
            raise CliError("The firewall helper returned an invalid state. Refresh to check protection.")
        return GuardStatus(data["state"], data["detail"])


class DemoGuard:
    demo = True

    def __init__(self):
        self.enabled = False

    def status(self):
        return GuardStatus("on" if self.enabled else "off", "Demo · VPN only " + ("on" if self.enabled else "off"))

    def change(self, enabled):
        self.enabled = enabled
        return self.status()
