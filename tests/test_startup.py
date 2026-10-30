from contextlib import ExitStack
from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from cyberghost_desktop import cli, firewall, startup_system as system
from cyberghost_desktop.cli import CliError, Selection
from cyberghost_desktop.startup import Autostart, DemoStartup, StartupManager, StartupState

SOURCE = Path(system.__file__).parent


def profile(selection=None, vpn_only=False):
    return {"managed_by": system.MARKER, "uid": 12345, "username": "desktop-test", "binary": "/trusted/cyberghostvpn",
            "selection": asdict(selection or Selection(country="DE")), "vpn_only": vpn_only}


class StartupDesktopTests(unittest.TestCase):
    def setUp(self):
        systemd = patch("cyberghost_desktop.startup.systemd_available", return_value=True)
        systemd.start()
        self.addCleanup(systemd.stop)

    def fixtures(self, directory, run=None):
        root = Path(directory)
        project = root / 'Project space % $name'
        project.mkdir()
        (project / "run.sh").write_text("#!/bin/sh\nexit 0\n")
        entry = Autostart(config=root / "config", project=project)
        manager = StartupManager(paths=system.Paths(12345, root), autostart=entry, run=run or Mock())
        return entry, manager

    def test_entry_is_staged_and_disabled_until_committed(self):
        with tempfile.TemporaryDirectory() as directory:
            entry, _ = self.fixtures(directory)
            staged = entry.stage()
            self.assertFalse(entry.path.exists())
            self.assertIn("Exec=/bin/sh", staged.read_text())
            self.assertIn("%%", staged.read_text())
            self.assertIn("\\$name", staged.read_text())
            entry.commit(staged)
            self.assertFalse(staged.exists())
            entry.remove()
            self.assertFalse(entry.path.exists())

    def test_non_systemd_host_has_no_boot_option_or_authorization(self):
        with tempfile.TemporaryDirectory() as directory, patch("cyberghost_desktop.startup.systemd_available", return_value=False):
            entry, manager = self.fixtures(directory)
            state = manager.inspect()
            self.assertFalse(state.available)
            self.assertFalse(state.enabled or state.running)
            self.assertIn("Manual VPN", state.detail)
            with patch.object(manager, "_authorize") as authorize, self.assertRaisesRegex(CliError, "systemd"):
                manager.configure(Selection(country="DE"), False)
            authorize.assert_not_called()
            self.assertFalse(entry.path.exists())

    def test_cancel_does_not_enable_or_replace_autostart(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Mock(return_value=subprocess.CompletedProcess([], 126, "", ""))
            entry, manager = self.fixtures(directory, run)
            with patch("cyberghost_desktop.startup.shutil.which", return_value="/usr/bin/pkexec"):
                with self.assertRaisesRegex(CliError, "cancelled"):
                    manager.configure(Selection(country="DE"), False)
            self.assertFalse(entry.path.exists())
            self.assertEqual(list(entry.path.parent.iterdir()), [])
            args, options = run.call_args
            self.assertEqual(args[0][:4], ["/usr/bin/pkexec", "--disable-internal-agent", "/usr/bin/python3", "-I"])
            self.assertEqual(args[0][-2], "configure")
            self.assertEqual(json.loads(args[0][-1])["selection"]["country"], "DE")
            self.assertNotIn("shell", options)
            self.assertNotIn("timeout", options)

    def test_success_commits_autostart_and_disable_removes_only_owned_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            entry, manager = self.fixtures(directory)
            with patch.object(manager, "_authorize") as authorize, patch.object(manager, "inspect", return_value=StartupState(True)):
                manager.configure(Selection(country="DE"), True)
            self.assertTrue(entry.path.exists())
            authorize.assert_called_once()
            unrelated = entry.path.parent / "other.desktop"
            unrelated.write_text("other application")
            with patch.object(manager, "_authorize") as authorize:
                self.assertFalse(manager.disable().enabled)
                authorize.assert_not_called()  # No system profile was installed by the fake authorization.
            self.assertEqual(unrelated.read_text(), "other application")

    def test_unrelated_or_symlink_entry_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            entry, _ = self.fixtures(directory)
            entry.path.parent.mkdir(parents=True)
            entry.path.write_text("unrelated")
            with self.assertRaises(RuntimeError):
                entry.stage()
            entry.path.unlink()
            victim = Path(directory) / "victim"
            victim.write_text(system.MARKER)
            entry.path.symlink_to(victim)
            with self.assertRaises(RuntimeError):
                entry.remove()
            self.assertTrue(victim.exists())

    def test_startup_status_disables_manual_actions_while_service_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Mock(return_value=subprocess.CompletedProcess([], 0, "MainPID=42\nJob=\nActiveState=active\nUnitFileState=enabled\n", ""))
            entry, manager = self.fixtures(directory, run)
            manager.paths.profile.parent.mkdir(parents=True)
            manager.paths.profile.write_text("placeholder")
            entry.commit(entry.stage())
            with patch("cyberghost_desktop.startup.read_profile", return_value=profile()):
                state = manager.inspect()
            self.assertTrue(state.running)
            self.assertIn("starting", state.detail)
            run.return_value = subprocess.CompletedProcess([], 0, "MainPID=0\nJob=123\nActiveState=inactive\nUnitFileState=enabled\n", "")
            with patch("cyberghost_desktop.startup.read_profile", return_value=profile()):
                self.assertTrue(manager.inspect().running, "Queued boot jobs must also block a concurrent manual connection")
            run.return_value = subprocess.CompletedProcess([], 0, "MainPID=0\nJob=\nActiveState=active\nUnitFileState=enabled\n", "")
            with patch("cyberghost_desktop.startup.read_profile", return_value=profile()):
                self.assertFalse(manager.inspect().running)
            run.return_value = subprocess.CompletedProcess([], 0, "", "")
            with patch("cyberghost_desktop.startup.read_profile", return_value=profile()), self.assertRaises(CliError):
                manager.inspect()

    def test_demo_does_not_install_or_autoconnect(self):
        with patch("cyberghost_desktop.startup.subprocess.run", side_effect=AssertionError("real command")):
            manager = DemoStartup()
            self.assertFalse(manager.inspect().enabled)
            self.assertTrue(manager.configure(Selection(country="DE"), True).enabled)
            self.assertFalse(manager.disable().enabled)

    def test_openvpn_cannot_enable_wireguard_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            _, manager = self.fixtures(directory)
            with patch.object(manager, "_authorize") as authorize, self.assertRaises(CliError):
                manager.configure(Selection(country="DE", protocol="openvpn"), True)
            authorize.assert_not_called()


class StartupInstallTests(unittest.TestCase):
    def test_public_status_and_profile_are_readable_with_restrictive_service_umask(self):
        with tempfile.TemporaryDirectory() as fixture:
            paths = system.Paths(12345, Path(fixture))
            previous = os.umask(0o077)
            try:
                with patch.object(system, "trusted", side_effect=lambda path: path):
                    system.write(paths.profile, json.dumps(profile()))
                    system.directory(paths.runtime.parent)
                    system.write(paths.runtime / "status.json", '{"state":"connected"}')
            finally:
                os.umask(previous)
            for path in (paths.profile.parent, paths.runtime.parent, paths.runtime):
                self.assertEqual(path.stat().st_mode & 0o777, 0o755)
            for path in (paths.profile, paths.runtime / "status.json"):
                self.assertEqual(path.stat().st_mode & 0o777, 0o644)

    def test_installs_protected_copies_and_enables_without_starting(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = system.Paths(12345, Path(directory))
            control = Mock(return_value="0")
            account = SimpleNamespace(pw_uid=12345)
            with patch.object(system, "profile_for", return_value=profile()), patch.object(system, "trusted", side_effect=lambda p: p.resolve()):
                system.install_profile(account, {}, paths=paths, source=SOURCE, control=control)
                self.assertEqual(json.loads(paths.profile.read_text()), profile())
                for name in system.COPIES:
                    self.assertEqual((paths.library / name).read_bytes(), (SOURCE / name).read_bytes())
                    self.assertEqual((paths.library / name).stat().st_mode & 0o777, 0o644)
                self.assertIn("Type=exec", paths.unit.read_text())
                self.assertIn("WantedBy=multi-user.target", paths.unit.read_text())
                self.assertIn("Restart=no", paths.unit.read_text())
                self.assertIn("/usr/local/libexec/", paths.unit.read_text())
                self.assertNotIn(str(SOURCE), paths.unit.read_text())
                self.assertTrue(any(call.args == ("enable", paths.name) for call in control.call_args_list))
                system.remove_profile(paths, control=control)
            self.assertFalse(paths.profile.exists())
            self.assertFalse(paths.unit.exists())
            self.assertFalse(paths.library.exists())
            self.assertTrue(all("--now" not in call.args and "stop" not in call.args and "restart" not in call.args
                                for call in control.call_args_list))

    def test_failed_enable_restores_previous_files(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = system.Paths(12345, Path(directory))
            account = SimpleNamespace(pw_uid=12345)
            with patch.object(system, "profile_for", return_value=profile()), patch.object(system, "trusted", side_effect=lambda p: p.resolve()):
                system.install_profile(account, {}, paths=paths, source=SOURCE, control=Mock(return_value="0"))
                before = paths.profile.read_bytes()

                def fail(*args):
                    if args[0] == "enable":
                        raise RuntimeError("authorization boundary failed")
                    return "0"

                with patch.object(system, "profile_for", return_value=profile(Selection(country="FR"))), self.assertRaises(RuntimeError):
                    system.install_profile(account, {}, paths=paths, source=SOURCE, control=fail)
                self.assertEqual(paths.profile.read_bytes(), before)

    def test_refuses_foreign_files_and_running_job(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = system.Paths(12345, Path(directory))
            paths.unit.parent.mkdir(parents=True)
            paths.unit.write_text("another application")
            with patch.object(system, "profile_for", return_value=profile()), self.assertRaisesRegex(RuntimeError, "another application"):
                system.install_profile(SimpleNamespace(pw_uid=12345), {}, paths=paths, source=SOURCE, control=Mock())
            paths.unit.write_text("# " + system.MARKER)
            with patch.object(system, "profile_for", return_value=profile()), self.assertRaisesRegex(RuntimeError, "finish"):
                system.install_profile(SimpleNamespace(pw_uid=12345), {}, paths=paths, source=SOURCE, control=Mock(return_value="33"))

    def test_rejects_untrusted_executables(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "cyberghostvpn"
            executable.write_text("untrusted")
            executable.chmod(0o777)
            with self.assertRaises(RuntimeError):
                system.trusted(executable)

    def test_profile_rejects_extra_fields_and_option_injection(self):
        account = SimpleNamespace(pw_uid=12345, pw_name="desktop-test")
        for payload in [{"selection": {}, "vpn_only": False, "command": "anything"},
                        {"selection": asdict(Selection(country="DE", city="--stop")), "vpn_only": False},
                        {"selection": asdict(Selection(country="DE\n--stop")), "vpn_only": False},
                        {"selection": asdict(Selection(country="DE", protocol="openvpn")), "vpn_only": True}]:
            with self.subTest(payload=payload), self.assertRaises((ValueError, TypeError)):
                system.profile_for(account, payload, SOURCE)


class BootRunnerTests(unittest.TestCase):
    def boot(self, *, initially=False, vpn_only=False, status_unknown=False, route_wrong=False, guard_present=False,
             offline=False, file_available=True, connect_exit=0, verify_guard=True):
        data = profile(vpn_only=vpn_only)
        connected = initially
        calls = []
        protected = Mock(return_value=firewall.GuardStatus("on" if verify_guard else "unknown", "checked"))

        def run(command, **kwargs):
            nonlocal connected
            calls.append((command, kwargs))
            tool = Path(command[0]).name
            if tool == "cyberghostvpn":
                if "--connect" in command:
                    connected = connect_exit == 0
                    return subprocess.CompletedProcess(command, connect_exit, "CLI finished", "")
                output = "unexpected output" if status_unknown else "Wireguard connection found." if connected else "No VPN connections found."
            elif tool == "ip":
                if "link" in command:
                    links = [{"ifname": "cyberghost", "flags": ["UP"], "linkinfo": {"info_kind": "wireguard"}}] if connected else []
                    output = json.dumps(links)
                else:
                    output = json.dumps([] if offline else [{"dev": "cyberghost" if connected and not route_wrong else "eth0"}])
            elif tool == "nft":
                output = json.dumps({"nftables": [{"table": {"family": "inet", "name": firewall.TABLE, "comment": firewall.OWNER}}] if guard_present else []})
            else:
                raise AssertionError(f"Unexpected command: {command}")
            return subprocess.CompletedProcess(command, 0, output, "")

        errors = []
        with ExitStack() as stack:
            stack.enter_context(patch.object(system, "read_profile", return_value=data))
            stack.enter_context(patch.object(system.pwd, "getpwuid", return_value=SimpleNamespace(pw_uid=12345, pw_name="desktop-test", pw_dir="/home/desktop-test")))
            stack.enter_context(patch.object(system, "trusted", side_effect=lambda path: path))
            stack.enter_context(patch.object(Path, "is_file", return_value=file_available))
            stack.enter_context(patch.object(system.shutil, "which", side_effect=lambda name, **_: "/usr/sbin/" + name))
            stack.enter_context(patch.object(system, "adapter", side_effect=lambda name, _directory:
                cli if name == "cli" else SimpleNamespace(Firewall=firewall.Firewall, authorized_action=protected)))
            output = stack.enter_context(patch.object(system, "outcome"))
            try:
                system.boot_connect(system.Paths(12345), run=run, sleep=lambda _: None, now=Mock(side_effect=[0, 181]))
            except RuntimeError as error:
                errors.append(str(error))
        return calls, protected, output, errors

    def test_connects_saved_profile_once_with_correct_account_then_enables_guard(self):
        calls, guard, output, errors = self.boot(vpn_only=True)
        self.assertFalse(errors)
        commands = [(command, kwargs) for command, kwargs in calls if "--connect" in command]
        self.assertEqual(len(commands), 1)
        command, kwargs = commands[0]
        self.assertEqual(command, ["/trusted/cyberghostvpn", "--traffic", "--country-code", "DE", "--wireguard", "--connect"])
        self.assertEqual(kwargs["env"]["SUDO_USER"], "desktop-test")
        self.assertIsNone(kwargs["timeout"])
        self.assertNotIn("shell", kwargs)
        guard.assert_called_once_with("enable")
        self.assertEqual(output.call_args.args[1], "connected")

    def test_existing_connection_is_not_replaced(self):
        calls, guard, _, errors = self.boot(initially=True)
        self.assertFalse(errors)
        self.assertFalse(any("--connect" in command for command, _ in calls))
        guard.assert_not_called()

    def test_unverified_vpn_or_existing_guard_never_reconnects(self):
        for options in ({"status_unknown": True}, {"guard_present": True}):
            with self.subTest(options=options):
                calls, guard, _, errors = self.boot(**options)
                self.assertTrue(errors)
                self.assertFalse(any("--connect" in command for command, _ in calls))
                guard.assert_not_called()

    def test_offline_and_missing_login_mounted_account_fail_without_connect(self):
        for options in ({"offline": True}, {"file_available": False}):
            with self.subTest(options=options):
                calls, guard, _, errors = self.boot(**options)
                self.assertTrue(errors)
                self.assertFalse(any("--connect" in command for command, _ in calls))
                guard.assert_not_called()

    def test_successful_cli_message_alone_does_not_prove_connection(self):
        calls, guard, _, errors = self.boot(route_wrong=True, vpn_only=True)
        self.assertTrue(errors)
        guard.assert_not_called()
        self.assertEqual(sum("--connect" in command for command, _ in calls), 1)

    def test_failed_connection_is_not_retried_and_does_not_enable_guard(self):
        calls, guard, _, errors = self.boot(connect_exit=1, vpn_only=True)
        self.assertTrue(errors)
        self.assertEqual(sum("--connect" in command for command, _ in calls), 1)
        guard.assert_not_called()

    def test_unverified_firewall_is_not_reported_as_protected(self):
        _, _, output, errors = self.boot(vpn_only=True, verify_guard=False)
        self.assertTrue(errors)
        self.assertNotEqual(output.call_args.args[1], "connected")


if __name__ == "__main__":
    unittest.main()
