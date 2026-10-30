"""Local setup checks and explicit, interactive runs of the official CLI installer.

This module also runs inside a terminal. It never captures account credentials,
downloads executables, changes VPN connections, or runs the desktop app as root.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import pwd
import shlex
import shutil
import subprocess

ACCOUNT_URL = "https://my.cyberghostvpn.com/"
INSTALL_GUIDE_URL = "https://support.cyberghostvpn.com/hc/en-us/articles/360020436274-Set-Up-CyberGhost-VPN-CLI-App-on-Linux"

# Each invocation passes an argument array and waits for the terminal's child.
TERMINALS = (
    ("x-terminal-emulator", ("-e",)),
    ("gnome-terminal", ("--wait", "--")),
    ("ptyxis", ("--standalone", "--")),
    ("konsole", ("--nofork", "-e")),
    ("xfce4-terminal", ("--disable-server", "--execute")),
    ("xterm", ("-e",)),
)


class SetupError(RuntimeError):
    pass


@dataclass(frozen=True)
class Installation:
    cli: str | None
    config_path: Path
    config_state: str  # present, missing, inaccessible; contents are never read
    pkexec: str | None
    sudo: str | None
    terminal: tuple[str, ...]

    @property
    def needs_setup(self) -> bool:
        return not self.cli or self.config_state != "present" or not self.pkexec


def find_terminal() -> tuple[str, ...]:
    for name, args in TERMINALS:
        executable = shutil.which(name)
        if executable:
            return (executable, *args)
    return ()


def inspect_installation(config_path: Path | None = None) -> Installation:
    if config_path is None:
        # CLI 1.5.0 derives this location from SUDO_USER, not from HOME.
        username = pwd.getpwuid(os.getuid()).pw_name
        config_path = Path("/home") / username / ".cyberghost/config.ini"
    try:
        config_state = "present" if config_path.is_file() else "missing"
    except OSError:
        config_state = "inaccessible"
    return Installation(shutil.which("cyberghostvpn"), config_path, config_state,
                        shutil.which("pkexec"), shutil.which("sudo"), find_terminal())


def installer_path(value: str | Path) -> Path:
    try:
        path = Path(value).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise SetupError("Choose install.sh from the extracted CyberGhost Linux download.") from error
    if path.name != "install.sh" or not path.is_file():
        raise SetupError("Choose install.sh from the extracted CyberGhost Linux download, not the ZIP archive.")
    return path


def task_command(action: str, installer: str | Path | None = None) -> tuple[list[str], Path | None]:
    sudo = shutil.which("sudo")
    if not sudo:
        raise SetupError("Install sudo with your distribution's package manager before running CLI setup.")
    if action == "install":
        if installer is None:
            raise SetupError("Select CyberGhost's extracted install.sh first.")
        path = installer_path(installer)
        bash = shutil.which("bash")
        if not bash:
            raise SetupError("Install Bash with your distribution's package manager before running CyberGhost's installer.")
        # The official installer expects its other files in its working directory.
        return [sudo, "--", bash, "--", str(path)], path.parent
    if action == "account":
        cli = shutil.which("cyberghostvpn")
        if not cli:
            raise SetupError("Install the CyberGhost CLI before configuring your account.")
        return [sudo, "--", cli, "--setup"], None
    raise SetupError("Unknown setup action.")


def terminal_command(action: str, installer: str | Path | None = None) -> list[str]:
    # Validate again in the terminal before execution, including after file moves.
    task_command(action, installer)
    terminal = find_terminal()
    if not terminal:
        raise SetupError("No supported terminal was found. Use the commands shown in Setup in your terminal.")
    command = [*terminal, "/usr/bin/python3", str(Path(__file__).resolve()), action]
    if action == "install":
        command.append(str(installer_path(installer)))
    return command


def run_in_terminal(action: str, installer: str | Path | None = None) -> None:
    command = terminal_command(action, installer)
    try:
        # The terminal owns all prompts and output. Nothing sensitive is read by
        # the GUI or copied into its activity log. No timeout kills installation.
        result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, check=False)
    except OSError as error:
        raise SetupError(f"Could not open the setup terminal: {error}") from error
    if result.returncode:
        raise SetupError("Setup did not complete successfully. Review the terminal message and check again.")


def run_task(action: str, installer: str | Path | None = None) -> int:
    command, directory = task_command(action, installer)
    print("CyberGhost CLI installation" if action == "install" else "CyberGhost account setup", flush=True)
    print("Enter any administrator and account credentials directly in this terminal.\n", flush=True)
    print(shlex.join(command), flush=True)
    # Inherit the terminal streams so sudo and CyberGhost can read passwords.
    return subprocess.run(command, cwd=directory, check=False).returncode


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Interactive CyberGhost CLI setup")
    parser.add_argument("action", choices=("install", "account"))
    parser.add_argument("installer", nargs="?")
    args = parser.parse_args(argv)
    try:
        code = run_task(args.action, args.installer)
    except (SetupError, OSError) as error:
        print(f"\nSetup could not run: {error}", flush=True)
        code = 1
    except KeyboardInterrupt:
        print("\nSetup cancelled.", flush=True)
        code = 130
    print("\nSetup command finished." if code == 0 else f"\nSetup stopped (exit code {code}).", flush=True)
    print("Return to CyberGhost Desktop and check the setup status.", flush=True)
    try:
        input("Press Enter to close this terminal…")
    except (EOFError, KeyboardInterrupt):
        pass
    return code


if __name__ == "__main__":
    raise SystemExit(main())
