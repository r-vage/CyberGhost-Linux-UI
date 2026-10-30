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
        helper = Path(__file__).with_name("firewall.py").resolve()
        if not helper.is_file():
            raise CliError("The VPN-only helper is missing. If the app folder was moved or renamed, "
                           "quit the app from the tray and reopen it from its updated launcher.", str(helper))
        from .authorization import control_command
        command = control_command(pkexec, "guard", {"enabled": enabled}) or [
            pkexec, "--disable-internal-agent", "/usr/bin/python3", "-I", str(helper), "enable" if enabled else "disable"]
        try:
            result = subprocess.run(command, stdin=subprocess.DEVNULL,
                                    capture_output=True, text=True, check=False)
        except OSError as error:
            raise CliError("Could not start the VPN-only authorization helper.", str(error)) from error
        if result.returncode in {126, 127}:
            raise CliError("Firewall authorization was cancelled or denied.")
        diagnostic = (result.stderr.strip() or result.stdout.strip())[:4000]
        try:
            data = json.loads(result.stdout)
        except ValueError:
            raise CliError(f"The VPN-only helper did not return a result (exit {result.returncode}). "
                           "See Activity for details. If the app was moved or updated, quit and reopen it.", diagnostic) from None
        if not isinstance(data, dict):
            raise CliError("The VPN-only helper returned an invalid response. Refresh to check protection.", diagnostic)
        if result.returncode or "error" in data:
            raise CliError(data.get("error", "Could not change VPN-only protection."))
        if data.get("state") not in {"on", "off", "unknown"} or not isinstance(data.get("detail"), str):
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
