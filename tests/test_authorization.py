from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import xml.etree.ElementTree as ET

from cyberghost_desktop import authorization as auth, authorization_helper as helper, authorization_install as installer
from cyberghost_desktop.cli import CyberGhostClient, Selection
from cyberghost_desktop.firewall import GuardStatus
from cyberghost_desktop.vpn_guard import VPNGuard

SOURCE = Path(auth.__file__).parent


class AuthorizationTests(unittest.TestCase):
    def test_cache_policy_targets_only_the_fixed_vpn_helper(self):
        root = ET.fromstring(installer.policy_text())
        actions = root.findall('action')
        self.assertEqual(len(actions), 1)
        action = actions[0]
        self.assertEqual(action.get('id'), auth.ACTION)
        self.assertNotEqual(action.get('id'), 'org.freedesktop.policykit.exec')
        self.assertEqual(action.findtext('defaults/allow_active'), 'auth_admin_keep')
        self.assertEqual(action.findtext('defaults/allow_any'), 'auth_admin')
        self.assertEqual(action.findtext('defaults/allow_inactive'), 'auth_admin')
        annotations = {item.get('key'): item.text for item in action.findall('annotate')}
        self.assertEqual(annotations, {'org.freedesktop.policykit.exec.path': str(auth.LIBRARY / 'command')})

    def test_install_update_remove_only_managed_files_and_preserve_modes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            library, policy = root / 'library', root / 'policies/control.policy'
            other = root / 'policies/other.policy'
            other.parent.mkdir()
            other.write_text('unrelated policy')
            with patch.object(installer.system, 'trusted'):
                installer.configure(True, source=SOURCE, library=library, policy=policy)
                self.assertEqual((library / 'command').stat().st_mode & 0o777, 0o755)
                self.assertIn('/usr/bin/python3 -I', (library / 'command').read_text())
                for name in auth.COPIES:
                    self.assertEqual((library / name).read_bytes(), (SOURCE / name).read_bytes())
                    self.assertEqual((library / name).stat().st_mode & 0o777, 0o644)
                with patch.object(auth, 'trusted'):
                    self.assertEqual(auth.status(library=library, policy=policy, source=SOURCE).state, 'ready')
                    (library / 'cli.py').write_text('outdated snapshot')
                    self.assertEqual(auth.status(library=library, policy=policy, source=SOURCE).state, 'outdated')
                    installer.configure(True, source=SOURCE, library=library, policy=policy)
                    self.assertEqual(auth.status(library=library, policy=policy, source=SOURCE).state, 'ready')
                installer.configure(False, library=library, policy=policy)
            self.assertFalse(library.exists())
            self.assertFalse(policy.exists())
            self.assertEqual(other.read_text(), 'unrelated policy')

    def test_unrecognized_or_symlinked_installation_is_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            library, policy = root / 'library', root / 'control.policy'
            policy.write_text('unrelated')
            with self.assertRaisesRegex(RuntimeError, 'unrelated'):
                installer.configure(True, library=library, policy=policy)
            self.assertEqual(policy.read_text(), 'unrelated')
            policy.unlink()
            library.mkdir()
            with patch.object(installer.system, 'trusted'), self.assertRaisesRegex(RuntimeError, 'unrelated'):
                installer.configure(False, library=library, policy=policy)

    def test_install_failure_rolls_back_before_policy_becomes_available(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            library, policy = root / 'library', root / 'control.policy'
            write = installer.system.write
            def failing_write(path, text, mode=0o644):
                if path == policy:
                    raise OSError('test policy write failed')
                return write(path, text, mode)
            with (patch.object(installer.system, 'trusted'), patch.object(installer.system, 'write', side_effect=failing_write),
                  self.assertRaises(OSError)):
                installer.configure(True, source=SOURCE, library=library, policy=policy)
            self.assertFalse(library.exists())
            self.assertFalse(policy.exists())

    def test_user_owned_snapshot_is_never_used_for_cached_authorization(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'helper'
            path.write_text('not a privileged helper')
            with patch.object(auth, 'trusted', side_effect=ValueError('not root-owned')):
                self.assertEqual(auth.status(library=path.parent, policy=path, source=SOURCE).state, 'unavailable')
            path.chmod(0o666)
            with self.assertRaises(ValueError):
                auth.trusted(path)

    def test_installer_is_not_run_through_the_cacheable_action(self):
        run = Mock(return_value=subprocess.CompletedProcess([], 0, '', ''))
        with (patch.object(auth.shutil, 'which', return_value='/usr/bin/pkexec'),
              patch.object(auth.subprocess, 'run', run), patch.object(auth, 'status', return_value=auth.AuthorizationStatus('ready', 'Ready'))):
            auth.configure(True)
        command = run.call_args.args[0]
        self.assertEqual(command[:4], ['/usr/bin/pkexec', '--disable-internal-agent', '/usr/bin/python3', '-I'])
        self.assertTrue(command[4].endswith('/authorization_install.py'))
        self.assertEqual(command[5], 'install')
        self.assertNotIn(str(auth.LIBRARY / 'command'), command)

    def test_connect_disconnect_and_guard_share_one_dedicated_action(self):
        run = Mock(return_value=subprocess.CompletedProcess([], 0, 'Done', ''))
        with (patch.object(auth, 'status', return_value=auth.AuthorizationStatus('ready', 'Ready')),
              patch('cyberghost_desktop.cli.shutil.which', return_value='/usr/bin/pkexec')):
            client = CyberGhostClient('/usr/bin/cyberghostvpn', run)
            client.connect(Selection(country='DE'))
            connect_command = run.call_args.args[0]
            client.disconnect()
            disconnect_command = run.call_args.args[0]
            result = subprocess.CompletedProcess([], 0, json.dumps(asdict(GuardStatus('on', 'Verified'))), '')
            with patch('cyberghost_desktop.vpn_guard.subprocess.run', return_value=result) as guard_run:
                VPNGuard().change(True)
            guard_command = guard_run.call_args.args[0]
        for command in (connect_command, disconnect_command, guard_command):
            self.assertEqual(command[:3], ['/usr/bin/pkexec', '--disable-internal-agent', str(auth.LIBRARY / 'command')])
        self.assertEqual([command[3] for command in (connect_command, disconnect_command, guard_command)], ['connect', 'disconnect', 'guard'])
        self.assertEqual(json.loads(connect_command[4])['selection']['country'], 'DE')

    def test_cached_helper_rejects_arbitrary_commands_and_malformed_actions(self):
        connection, firewall, trust = Mock(), Mock(), Mock()
        for action, payload in [('shell', {'command': 'anything'}), ('install', {}), ('disconnect', {'command': 'anything'}),
                                ('guard', {'enabled': 'yes'}), ('connect', {'selection': {}})]:
            with self.subTest(action=action), self.assertRaises(ValueError):
                helper.dispatch(action, payload, SimpleNamespace(), connection, firewall, trust)
        trust.assert_not_called()
        connection.protected_connect.assert_not_called()
        firewall.authorized_action.assert_not_called()

    def test_cached_helper_checks_the_installed_cli_before_using_it(self):
        connection, firewall = Mock(), Mock()
        connection.cli.Selection = Selection
        connection.system_tool.return_value = '/usr/bin/cyberghostvpn'
        payload = {'selection': asdict(Selection(country='DE')), 'vpn_only': True, 'replace_existing': True}
        trust = Mock(side_effect=RuntimeError('untrusted binary'))
        with self.assertRaisesRegex(RuntimeError, 'untrusted'):
            helper.dispatch('connect', payload, SimpleNamespace(), connection, firewall, trust)
        connection.protected_connect.assert_not_called()


if __name__ == '__main__':
    unittest.main()
