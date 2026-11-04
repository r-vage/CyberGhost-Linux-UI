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


def profile(selection=None, vpn_only=False, block_until_connected=False):
    return {"managed_by": system.MARKER, "uid": 12345, "username": "desktop-test", "binary": "/trusted/cyberghostvpn",
            "selection": asdict(selection or Selection(country="DE")), "vpn_only": vpn_only,
            "block_until_connected": block_until_connected}


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

    def test_strict_boot_implies_protection_and_requires_wireguard(self):
        with tempfile.TemporaryDirectory() as directory:
            entry, manager = self.fixtures(directory)
            with patch.object(manager, "_authorize") as authorize, patch.object(manager, "inspect", return_value=StartupState(True)):
                manager.configure(Selection(country="DE"), False, True)
            self.assertTrue(authorize.call_args.args[1]["vpn_only"])
            self.assertTrue(authorize.call_args.args[1]["block_until_connected"])
            with patch.object(manager, "_authorize") as authorize, self.assertRaisesRegex(CliError, "WireGuard"):
                manager.configure(Selection(country="DE", protocol="openvpn"), False, True)
            authorize.assert_not_called()

    def test_legacy_profile_does_not_enable_strict_boot(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Mock(return_value=subprocess.CompletedProcess([], 0, 'MainPID=0\nJob=0\nActiveState=active\nUnitFileState=enabled\n', ''))
            _, manager = self.fixtures(directory, run)
            data = profile(vpn_only=True)
            del data['block_until_connected']
            manager.paths.profile.parent.mkdir(parents=True)
            manager.paths.profile.write_text(json.dumps(data))
            with patch.object(system, 'trusted', side_effect=lambda path: path):
                state = manager.inspect()
            self.assertTrue(state.vpn_only)
            self.assertFalse(state.block_until_connected)

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

    def test_boot_selection_requires_a_destination_recorded_by_the_boot_runner(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Mock(return_value=subprocess.CompletedProcess([], 0,
                       "MainPID=0\nJob=\nActiveState=active\nUnitFileState=enabled\n", ""))
            entry, manager = self.fixtures(directory, run)
            entry.commit(entry.stage())
            manager.paths.profile.parent.mkdir(parents=True)
            manager.paths.profile.write_text("placeholder")
            os.utime(manager.paths.profile, ns=(1000, 1000))
            manager.paths.runtime.mkdir(parents=True)
            report = manager.paths.runtime / "status.json"
            with patch("cyberghost_desktop.startup.read_profile", return_value=profile()):
                self.assertIsNone(manager.inspect().boot_selection)
                report.write_text(json.dumps({"state": "connected", "detail": "Existing connection"}))
                self.assertIsNone(manager.inspect().boot_selection, "An existing tunnel has no verified saved destination")
                for state in ("connecting", "failed", "connected"):
                    report.write_text(json.dumps({"state": state, "detail": "Boot outcome", "selection": profile()["selection"]}))
                    os.utime(report, ns=(2000, 2000))
                    expected = Selection(country="DE") if state == "connected" else None
                    self.assertEqual(manager.inspect().boot_selection, expected)
                # A still-running service cannot establish the comparison yet.
                run.return_value.stdout = "MainPID=42\nJob=\nActiveState=active\nUnitFileState=enabled\n"
                self.assertIsNone(manager.inspect().boot_selection)
                run.return_value.stdout = "MainPID=0\nJob=\nActiveState=active\nUnitFileState=enabled\n"
                # Saving a different destination prepares the next boot only.
                os.utime(manager.paths.profile, ns=(3000, 3000))
                self.assertEqual(manager.inspect().boot_selection, Selection(country="DE"))
                report.write_text(json.dumps({"state": "connected", "selection": {"country": "--stop"}}))
                self.assertIsNone(manager.inspect().boot_selection)

    def test_openvpn_cannot_enable_wireguard_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            _, manager = self.fixtures(directory)
            with patch.object(manager, "_authorize") as authorize, self.assertRaises(CliError):
                manager.configure(Selection(country="DE", protocol="openvpn"), True)
            authorize.assert_not_called()

    def test_historical_success_does_not_hide_current_startup_problems(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Mock(return_value=subprocess.CompletedProcess([], 0,
                       "MainPID=0\nJob=\nActiveState=active\nUnitFileState=disabled\n", ""))
            entry, manager = self.fixtures(directory, run)
            entry.commit(entry.stage())
            manager.paths.profile.parent.mkdir(parents=True)
            manager.paths.profile.write_text("placeholder")
            manager.paths.runtime.mkdir(parents=True)
            report = manager.paths.runtime / "status.json"
            report.write_text(json.dumps({"state": "connected", "detail": "Previous success", "selection": profile()["selection"]}))
            with patch("cyberghost_desktop.startup.read_profile", return_value=profile()):
                self.assertIn("not enabled", manager.inspect().detail)
                run.return_value.stdout = "MainPID=0\nJob=\nActiveState=failed\nUnitFileState=enabled\n"
                state = manager.inspect()
                self.assertIn("failed", state.detail)
                self.assertIsNone(state.boot_selection)
                run.return_value.stdout = "MainPID=0\nJob=\nActiveState=active\nUnitFileState=enabled\n"
                entry.remove()
                self.assertIn("desktop startup is missing", manager.inspect().detail)
                for malformed in ("[]", "null", "42", '"bad"'):
                    report.write_text(malformed)
                    state = manager.inspect()
                    self.assertFalse(state.running)
                    self.assertIsNone(state.boot_selection)


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

    def test_strict_boot_install_downgrade_and_removal_leave_live_services_alone(self):
        for remove in (False, True):
            with self.subTest(remove=remove), tempfile.TemporaryDirectory() as directory:
                paths = system.Paths(12345, Path(directory))
                account = SimpleNamespace(pw_uid=12345)
                control = Mock(return_value='0')
                with (patch.object(system, 'profile_for', return_value=profile(vpn_only=True, block_until_connected=True)),
                      patch.object(system, 'trusted', side_effect=lambda path: path.resolve())):
                    system.install_profile(account, {}, paths=paths, source=SOURCE, control=control)
                    self.assertIn('Type=oneshot', paths.guard_unit.read_text())
                    self.assertIn('DefaultDependencies=no', paths.guard_unit.read_text())
                    self.assertIn('Before=network-pre.target shutdown.target', paths.guard_unit.read_text())
                    self.assertIn('Requires=' + paths.guard_name, paths.unit.read_text())
                    for item in paths.network_dropins:
                        self.assertIn('Requires=' + paths.guard_name, item.read_text())
                        self.assertIn('After=' + paths.guard_name, item.read_text())
                    self.assertTrue(any(call.args == ('enable', paths.guard_name) for call in control.call_args_list))
                    if remove:
                        system.remove_profile(paths, control=control)
                    else:
                        with patch.object(system, 'profile_for', return_value=profile()):
                            system.install_profile(account, {}, paths=paths, source=SOURCE, control=control)
                        self.assertNotIn(paths.guard_name, paths.unit.read_text())
                self.assertFalse(paths.guard_unit.exists())
                self.assertFalse(any(path.exists() for path in paths.network_dropins))
                self.assertTrue(any(call.args == ('disable', paths.guard_name) for call in control.call_args_list))
                self.assertTrue(all(not {'--now', 'start', 'stop', 'restart'} & set(call.args) for call in control.call_args_list))

    def test_failed_strict_change_restores_units_dropins_and_enablement(self):
        for was_strict in (False, True):
            with self.subTest(was_strict=was_strict), tempfile.TemporaryDirectory() as directory:
                paths = system.Paths(12345, Path(directory))
                account = SimpleNamespace(pw_uid=12345)
                enabled = set()
                fail = False

                def control(*args):
                    if args[0] == 'enable':
                        if fail and args[1] == paths.name:
                            raise RuntimeError('failed enable')
                        enabled.add(args[1])
                    elif args[0] == 'disable':
                        enabled.discard(args[1])
                    return '0'

                with (patch.object(system, 'profile_for', return_value=profile(block_until_connected=was_strict)),
                      patch.object(system, 'trusted', side_effect=lambda path: path.resolve())):
                    system.install_profile(account, {}, paths=paths, source=SOURCE, control=control)
                    tracked = [paths.profile, paths.unit, paths.guard_unit, *paths.network_dropins]
                    before = {p: p.read_bytes() if p.exists() else None for p in tracked}
                    previous_enabled = enabled.copy()
                    fail = True
                    with patch.object(system, 'profile_for', return_value=profile(block_until_connected=not was_strict)), self.assertRaisesRegex(RuntimeError, 'failed enable'):
                        system.install_profile(account, {}, paths=paths, source=SOURCE, control=control)
                    self.assertEqual(enabled, previous_enabled)
                    self.assertEqual({p: p.read_bytes() if p.exists() else None for p in tracked}, before)

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
                        {"selection": asdict(Selection(country="DE")), "vpn_only": False, "block_until_connected": "yes"},
                        {"selection": asdict(Selection(country="DE", protocol="openvpn")), "vpn_only": False, "block_until_connected": True},
                        {"selection": asdict(Selection(country="DE", protocol="openvpn")), "vpn_only": True}]:
            with self.subTest(payload=payload), self.assertRaises((ValueError, TypeError)):
                system.profile_for(account, payload, SOURCE)


class BootRunnerTests(unittest.TestCase):
    def boot(self, *, initially=False, vpn_only=False, status_unknown=False, route_wrong=False, guard_present=False,
             offline=False, file_available=True, connect_exit=0, verify_guard=True, strict=False, dns_available=True):
        data = profile(vpn_only=vpn_only, block_until_connected=strict)
        connected = initially
        calls = []
        protected = Mock(return_value=firewall.GuardStatus("on" if verify_guard else "unknown", "checked"))

        def connect(selection, account):
            nonlocal connected
            self.assertTrue(strict)
            self.assertEqual(selection.country, 'DE')
            self.assertEqual(account.pw_name, 'desktop-test')
            calls.append((['protected-connect'], {}))
            if connect_exit:
                raise RuntimeError('CLI failed; protection remains enabled')
            connected = True

        adapters = {'cli': cli,
                    'firewall': SimpleNamespace(Firewall=firewall.Firewall, authorized_action=protected,
                                                request=Mock(return_value=protected.return_value)),
                    'protected_connect': SimpleNamespace(direct_resolvers=Mock(side_effect=None if dns_available else RuntimeError('No DNS')),
                                                          protected_connect=connect)}

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
            stack.enter_context(patch.object(system, "adapter", side_effect=lambda name, _directory: adapters[name]))
            output = stack.enter_context(patch.object(system, "outcome"))
            try:
                system.boot_connect(system.Paths(12345), run=run, sleep=lambda _: None, now=Mock(side_effect=[0, 181]))
            except RuntimeError as error:
                errors.append(str(error))
        return calls, protected, output, errors

    def test_early_protection_needs_neither_home_nor_network_nor_existing_vpn(self):
        for verified in (True, False):
            with self.subTest(verified=verified):
                module = SimpleNamespace(authorized_action=Mock(return_value=firewall.GuardStatus('on' if verified else 'unknown', 'checked')))
                with (patch.object(system, 'read_profile', return_value=profile(block_until_connected=True)),
                      patch.object(system, 'trusted'), patch.object(system, 'adapter', return_value=module),
                      patch.object(system, 'outcome') as output,
                      patch.object(system.pwd, 'getpwuid', side_effect=AssertionError('Home/account must not be needed'))):
                    if verified:
                        system.boot_protect(system.Paths(12345))
                        self.assertEqual(output.call_args.args[1], 'waiting')
                    else:
                        with self.assertRaisesRegex(RuntimeError, 'Network startup is blocked'):
                            system.boot_protect(system.Paths(12345))
                        output.assert_not_called()
                    module.authorized_action.assert_called_once_with('block')

    def test_strict_boot_uses_isolated_connector_and_never_unprotected_cli(self):
        calls, guard, output, errors = self.boot(strict=True, guard_present=True)
        self.assertFalse(errors)
        self.assertEqual(sum(command == ['protected-connect'] for command, _ in calls), 1)
        self.assertFalse(any('--connect' in command for command, _ in calls))
        guard.assert_called_once_with('enable')
        self.assertEqual(output.call_args.args[1], 'connected')

    def test_strict_failure_has_no_unprotected_fallback_or_disable(self):
        for options in ({'connect_exit': 1}, {'offline': True}, {'file_available': False}, {'verify_guard': False}, {'dns_available': False}):
            with self.subTest(options=options):
                calls, guard, output, errors = self.boot(strict=True, guard_present=True, **options)
                self.assertTrue(errors)
                self.assertFalse(any('--connect' in command for command, _ in calls))
                guard.assert_not_called()
                self.assertTrue(all(call.args[1] != 'connected' for call in output.call_args_list))

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
        calls, guard, output, errors = self.boot(initially=True)
        self.assertFalse(errors)
        self.assertFalse(any("--connect" in command for command, _ in calls))
        self.assertIsNone(output.call_args.kwargs["selection"])
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
