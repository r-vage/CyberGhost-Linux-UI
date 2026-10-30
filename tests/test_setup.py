import contextlib
import io
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from cyberghost_desktop import setup_tasks as setup


def commands(**overrides):
    available = {"sudo": "/usr/bin/sudo", "cyberghostvpn": "/usr/bin/cyberghostvpn", "pkexec": "/usr/bin/pkexec",
                 "x-terminal-emulator": "/usr/bin/x-terminal-emulator", "bash": "/usr/bin/bash"}
    available.update(overrides)
    return patch.object(setup.shutil, "which", side_effect=available.get)


class SetupTests(unittest.TestCase):
    def test_missing_cli_and_configuration_require_setup(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(setup.shutil, "which", return_value=None):
            state = setup.inspect_installation(Path(directory) / "config.ini")
        self.assertTrue(state.needs_setup)
        self.assertEqual(state.config_state, "missing")
        self.assertIsNone(state.cli)
        self.assertFalse(state.terminal)

    def test_existing_config_is_checked_without_reading_credentials(self):
        with tempfile.TemporaryDirectory() as directory, commands():
            config = Path(directory) / "config.ini"
            config.touch()
            with (patch.object(Path, "read_text", side_effect=AssertionError("must not read credentials")),
                  patch.object(Path, "read_bytes", side_effect=AssertionError("must not read credentials"))):
                state = setup.inspect_installation(config)
        self.assertFalse(state.needs_setup)
        self.assertEqual(state.config_state, "present")

    def test_inaccessible_config_is_not_reported_as_missing(self):
        with commands(), patch.object(Path, "is_file", side_effect=PermissionError):
            state = setup.inspect_installation(Path("/restricted/config.ini"))
        self.assertEqual(state.config_state, "inaccessible")
        self.assertTrue(state.needs_setup)

    def test_config_location_matches_cli_account_lookup(self):
        with (commands(), patch.object(setup.os, "getuid", return_value=1234),
              patch.object(setup.pwd, "getpwuid", return_value=SimpleNamespace(pw_name="example")) as lookup):
            state = setup.inspect_installation()
        lookup.assert_called_once_with(1234)
        self.assertEqual(state.config_path, Path("/home/example/.cyberghost/config.ini"))

    def test_recheck_redetects_newly_installed_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.ini"
            with commands(cyberghostvpn=None):
                self.assertIsNone(setup.inspect_installation(config).cli)
            with commands():
                self.assertEqual(setup.inspect_installation(config).cli, "/usr/bin/cyberghostvpn")

    def test_installer_rejects_archive_directory_and_missing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "download.zip"
            archive.touch()
            for path in (archive, directory, Path(directory) / "install.sh"):
                with self.subTest(path=path), self.assertRaises(setup.SetupError):
                    setup.installer_path(path)

    def test_installer_runs_from_download_directory_with_literal_path(self):
        with tempfile.TemporaryDirectory(prefix="cyberghost setup ; ") as directory, commands():
            path = Path(directory) / "install.sh"
            path.touch()
            command, cwd = setup.task_command("install", path)
        self.assertEqual(command, ["/usr/bin/sudo", "--", "/usr/bin/bash", "--", str(path)])
        self.assertEqual(cwd, path.parent)

    def test_actual_runner_preserves_installer_working_directory(self):
        # A local, unprivileged sudo stand-in executes a harmless test installer.
        # The production runner does the real subprocess and working-dir handling.
        with tempfile.TemporaryDirectory(prefix="cyberghost setup ; ") as directory:
            root = Path(directory)
            fake_sudo = root / "sudo"
            fake_sudo.write_text("#!/usr/bin/python3\nimport os, sys\nos.execv(sys.argv[2], sys.argv[2:])\n")
            fake_sudo.chmod(0o700)
            installer = root / "install.sh"
            installer.write_text("#!/bin/sh\npwd > installed-from.txt\n")
            with commands(sudo=str(fake_sudo)), contextlib.redirect_stdout(io.StringIO()):
                code = setup.run_task("install", installer)
            self.assertEqual(code, 0)
            self.assertEqual((root / "installed-from.txt").read_text().strip(), str(root))

    def test_account_setup_uses_sudo_and_inherits_terminal_streams(self):
        with (commands(), patch.object(setup.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as run,
              contextlib.redirect_stdout(io.StringIO())):
            self.assertEqual(setup.run_task("account"), 0)
        self.assertEqual(run.call_args.args[0], ["/usr/bin/sudo", "--", "/usr/bin/cyberghostvpn", "--setup"])
        self.assertNotIn("stdin", run.call_args.kwargs)
        self.assertNotIn("stdout", run.call_args.kwargs)
        self.assertFalse(run.call_args.kwargs.get("capture_output", False))
        self.assertFalse(run.call_args.kwargs.get("shell", False))

    def test_missing_cli_or_sudo_cannot_start_setup(self):
        for overrides in ({"cyberghostvpn": None}, {"sudo": None}):
            with self.subTest(overrides=overrides), commands(**overrides), self.assertRaises(setup.SetupError):
                setup.task_command("account")

    def test_installer_requires_bash_but_account_setup_does_not(self):
        with tempfile.TemporaryDirectory() as directory, commands(bash=None):
            path = Path(directory) / "install.sh"
            path.touch()
            with self.assertRaisesRegex(setup.SetupError, "Install Bash"):
                setup.task_command("install", path)
            self.assertEqual(setup.task_command("account")[0][-1], "--setup")

    def test_ptyxis_uses_standalone_instance_and_literal_arguments(self):
        with commands(**{"x-terminal-emulator": None, "ptyxis": "/usr/bin/ptyxis"}):
            command = setup.terminal_command("account")
        self.assertEqual(command[:4], ["/usr/bin/ptyxis", "--standalone", "--", "/usr/bin/python3"])
        self.assertEqual(command[-1], "account")

    def test_terminal_fallback_waits_and_passes_separate_arguments(self):
        with commands(**{"x-terminal-emulator": None, "gnome-terminal": "/usr/bin/gnome-terminal"}):
            command = setup.terminal_command("account")
        self.assertEqual(command[:4], ["/usr/bin/gnome-terminal", "--wait", "--", "/usr/bin/python3"])
        self.assertEqual(Path(command[4]), Path(setup.__file__).resolve())
        self.assertEqual(command[5:], ["account"])

    def test_missing_terminal_reports_manual_alternative(self):
        with commands(**{"x-terminal-emulator": None}), self.assertRaisesRegex(setup.SetupError, "commands shown in Setup"):
            setup.terminal_command("account")

    def test_terminal_failure_does_not_report_success_or_capture_credentials(self):
        run = Mock(return_value=SimpleNamespace(returncode=1))
        with commands(), patch.object(setup.subprocess, "run", run), self.assertRaisesRegex(setup.SetupError, "did not complete"):
            setup.run_in_terminal("account")
        self.assertEqual(run.call_args.kwargs["stdout"], subprocess.DEVNULL)
        self.assertEqual(run.call_args.kwargs["stderr"], subprocess.DEVNULL)
        self.assertFalse(run.call_args.kwargs.get("shell", False))
        self.assertNotIn("timeout", run.call_args.kwargs)

    def test_runner_keeps_failure_visible_until_terminal_acknowledgement(self):
        with (patch.object(setup, "run_task", return_value=5), patch("builtins.input", return_value="") as acknowledge,
              contextlib.redirect_stdout(io.StringIO()) as output):
            code = setup.main(["account"])
        self.assertEqual(code, 5)
        acknowledge.assert_called_once()
        self.assertIn("exit code 5", output.getvalue())


if __name__ == "__main__":
    unittest.main()
