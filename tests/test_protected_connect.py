from contextlib import contextmanager
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from cyberghost_desktop import protected_connect as reconnect
from cyberghost_desktop.cli import CliError, CyberGhostClient, Selection
from cyberghost_desktop.firewall import GuardStatus, TABLE, fingerprint


class ReconnectTests(unittest.TestCase):
    def test_one_authorization_for_connection_with_protection(self):
        run = Mock(return_value=subprocess.CompletedProcess([], 0, 'Connected; protection remains enabled.', ''))
        client = CyberGhostClient('/usr/bin/cyberghostvpn', run=run)
        selection = Selection(country='DE', city='Frankfurt', server='frankfurt-s415-i01')
        with patch('cyberghost_desktop.cli.shutil.which', return_value='/usr/bin/pkexec'):
            client.connect(selection, vpn_only=True)
        run.assert_called_once()
        command = run.call_args.args[0]
        self.assertEqual(command[:4], ['/usr/bin/pkexec', '--disable-internal-agent', '/usr/bin/python3', '-I'])
        self.assertTrue(command[4].endswith('/protected_connect.py'))
        self.assertEqual(json.loads(command[5]), {"selection": asdict(selection), "vpn_only": True, "replace_existing": False})
        self.assertIsNone(run.call_args.kwargs['timeout'])
        self.assertNotIn('disable', command)

    def test_server_switch_uses_one_authorization_for_both_steps(self):
        run = Mock(return_value=subprocess.CompletedProcess([], 0, 'Switched', ''))
        selection = Selection(country='DE', city='Frankfurt')
        with patch('cyberghost_desktop.cli.shutil.which', return_value='/usr/bin/pkexec'):
            CyberGhostClient('/usr/bin/cyberghostvpn', run).connect(selection, replace_existing=True)
        run.assert_called_once()
        self.assertEqual(json.loads(run.call_args.args[0][-1]),
                         {'selection': asdict(selection), 'vpn_only': False, 'replace_existing': True})

    def test_old_connection_is_verified_down_before_starting_the_new_one(self):
        account = SimpleNamespace(pw_name='test-user', pw_dir='/home/test-user')
        status = Mock(side_effect=[SimpleNamespace(state='connected'), SimpleNamespace(state='disconnected')])
        results = [subprocess.CompletedProcess([], 0, 'Disconnected', ''), subprocess.CompletedProcess([], 0, 'Connected', '')]
        with (patch.object(reconnect, 'system_tool', return_value='/usr/bin/cyberghostvpn'),
              patch.object(reconnect.shutil, 'which', return_value=None),
              patch.object(reconnect.cli.CyberGhostClient, 'status', status),
              patch.object(reconnect.subprocess, 'run', side_effect=results) as run):
            output = reconnect.switch_unprotected(Selection(country='DE'), account)
        self.assertEqual(run.call_args_list[0].args[0], ['/usr/bin/cyberghostvpn', '--stop'])
        self.assertIn('--connect', run.call_args_list[1].args[0])
        self.assertIn('Disconnected', output)
        self.assertEqual(status.call_count, 2)

    def test_disconnect_failure_prevents_new_connection(self):
        account = SimpleNamespace(pw_name='test-user', pw_dir='/home/test-user')
        status = Mock(return_value=SimpleNamespace(state='connected'))
        with (patch.object(reconnect, 'system_tool', return_value='/usr/bin/cyberghostvpn'),
              patch.object(reconnect.shutil, 'which', return_value=None),
              patch.object(reconnect.cli.CyberGhostClient, 'status', status),
              patch.object(reconnect.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, 'Still connected', '')) as run,
              self.assertRaisesRegex(CliError, 'new connection was not started')):
            reconnect.switch_unprotected(Selection(country='DE'), account)
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0][-1], '--stop')

    def test_openvpn_never_bypasses_the_wireguard_guard(self):
        run = Mock()
        with self.assertRaisesRegex(CliError, 'WireGuard'):
            CyberGhostClient('/usr/bin/cyberghostvpn', run=run).connect(Selection(country='DE', protocol='openvpn'), vpn_only=True)
        run.assert_not_called()

    def test_scope_rule_is_root_and_cgroup_specific(self):
        source = reconnect.connection_rule('cyberghost-desktop-connect-0123')
        self.assertIn('meta skuid 0 socket cgroupv2 level 1 "cyberghost-desktop-connect-0123" accept', source)
        self.assertNotIn('policy accept', source)
        self.assertNotIn('delete table', source)
        for invalid in ('', '/system.slice', '../escape', 'foo/../bar', 'foo" accept', 'foo\nbar'):
            with self.subTest(group=invalid), self.assertRaises(ValueError):
                reconnect.connection_rule(invalid)

    def test_dns_uses_configured_uplink_and_does_not_open_system_resolver(self):
        with tempfile.TemporaryDirectory() as directory:
            stub, uplink = Path(directory) / 'stub', Path(directory) / 'uplink'
            stub.write_text('nameserver 127.0.0.53\nnameserver ::1\n')
            uplink.write_text('nameserver 192.0.2.53\nnameserver 2001:db8::53 # IPv6\nnameserver 192.0.2.53\n')
            result = reconnect.direct_resolvers([stub, uplink])
            self.assertIn('nameserver 192.0.2.53\nnameserver 2001:db8::53\n', result)
            self.assertEqual(result.count('192.0.2.53'), 1)
            self.assertNotIn('127.0.0.53', result)
            with self.assertRaisesRegex(RuntimeError, 'remains enabled'):
                reconnect.direct_resolvers([stub])

    def test_only_our_temporary_rule_is_removed(self):
        guard = Mock()
        guard.document.return_value = {'nftables': [
            {'rule': {'chain': 'output', 'comment': 'our-group', 'handle': 42}},
            {'rule': {'chain': 'output', 'comment': 'other', 'handle': 43}},
            {'rule': {'chain': 'forward', 'comment': 'our-group', 'handle': 44}},
        ]}
        reconnect.remove_exception(guard, 'our-group')
        guard.command.assert_called_once_with('nft', '--file', '-', source=f'delete rule inet {TABLE} output handle 42\n')

    def test_connection_failures_restore_rules_without_disabling_guard(self):
        for failure in ('setup', 'cli', 'route', 'monitor', 'exception', 'success'):
            with self.subTest(outcome=failure), tempfile.TemporaryDirectory() as directory:
                runtime = Path(directory)
                original = {'nftables': [{'table': {'name': TABLE}}, {'rule': {'chain': 'output', 'expr': ['drop']}}]}
                current = json.loads(json.dumps(original))
                guard = Mock()
                guard.document.side_effect = lambda: json.loads(json.dumps(current))
                calls = []

                def command(*args, source):
                    calls.append(source)
                    if source.startswith('insert rule'):
                        current['nftables'].append({'rule': {'chain': 'output', 'comment': 'cyberghost-desktop-connect-0123', 'handle': 99}})
                    elif source.startswith('delete rule'):
                        current['nftables'].pop()
                    else:
                        raise AssertionError(source)

                guard.command.side_effect = command
                guard.wireguard_mark.side_effect = RuntimeError('No VPN route') if failure == 'route' else None
                @contextmanager
                def group():
                    if failure == 'setup':
                        raise RuntimeError('cgroup isolation unavailable')
                    yield Path('/unused/cyberghost-desktop-connect-0123')

                result = subprocess.CompletedProcess([], 1 if failure == 'cli' else 0, 'CLI output', '')
                with (patch.object(reconnect, 'system_tool', side_effect=lambda name: '/usr/bin/' + name),
                      patch.object(reconnect, 'direct_resolvers', return_value='nameserver 192.0.2.53\n'),
                      patch.object(reconnect.firewall, 'secure_runtime'),
                      patch.object(reconnect.firewall, 'request', return_value=GuardStatus('on', 'Verified')),
                      patch.object(reconnect.firewall, 'Firewall', return_value=guard),
                      patch.object(reconnect.firewall, 'authorized_action', return_value=GuardStatus('on', 'Verified'),
                                   side_effect=RuntimeError('Monitor verification failed') if failure == 'monitor' else None) as enable,
                      patch.object(reconnect, 'connection_group', group),
                      patch.object(reconnect.subprocess, 'run', return_value=result,
                                   side_effect=OSError('launch failed') if failure == 'exception' else None)):
                    account = SimpleNamespace(pw_name='desktop-user', pw_dir='/home/desktop-user')
                    if failure == 'success':
                        output = reconnect.protected_connect(Selection(country='DE'), account, runtime=runtime)
                        self.assertIn('protection remained enabled', output)
                        enable.assert_called_once()
                        self.assertEqual(enable.call_args.args[:2], ('enable', runtime))
                    else:
                        with self.assertRaises((RuntimeError, OSError)) as raised:
                            reconnect.protected_connect(Selection(country='DE'), account, runtime=runtime)
                        if failure in {'route', 'monitor'}:
                            self.assertIsInstance(raised.exception, CliError)
                            self.assertIn('CLI output', raised.exception.details)
                            self.assertNotIn('before enabling VPN only', str(raised.exception))
                        if failure == 'monitor':
                            enable.assert_called_once()
                        else:
                            enable.assert_not_called()
                guard.disable.assert_not_called()
                self.assertEqual(fingerprint(current), fingerprint(original))
                self.assertEqual(len(calls), 0 if failure == 'setup' else 2)
                self.assertFalse(list(runtime.glob('connect-*')))

    def test_protected_switch_refreshes_dns_after_disconnect_and_cleans_up_on_failure(self):
        for failure in (False, True):
            with self.subTest(disconnect_fails=failure), tempfile.TemporaryDirectory() as directory:
                runtime = Path(directory)
                account = SimpleNamespace(pw_name='desktop-user', pw_dir='/home/desktop-user')
                guard = Mock()
                guard.document.return_value = {'nftables': []}
                events = []

                def disconnect(*_args):
                    events.append('disconnect')
                    if failure:
                        raise CliError('Cannot disconnect')
                    return 'Disconnected'

                def launch(command, **_kwargs):
                    events.append('connect')
                    self.assertEqual(events, ['disconnect', 'connect'])
                    self.assertEqual((Path(command[-1]) / 'resolv.conf').read_text(), 'nameserver 192.0.2.53\n')
                    return subprocess.CompletedProcess(command, 0, 'Connected', '')

                @contextmanager
                def group():
                    yield Path('/unused/cyberghost-desktop-connect-0123')

                with (patch.object(reconnect, 'system_tool', side_effect=lambda name: '/usr/bin/' + name),
                      patch.object(reconnect, 'direct_resolvers', side_effect=['nameserver 192.0.2.99\n', 'nameserver 192.0.2.53\n']),
                      patch.object(reconnect.firewall, 'secure_runtime'),
                      patch.object(reconnect.firewall, 'request', return_value=GuardStatus('on', 'Verified')),
                      patch.object(reconnect.firewall, 'Firewall', return_value=guard),
                      patch.object(reconnect.firewall, 'authorized_action', return_value=GuardStatus('on', 'Verified')) as enable,
                      patch.object(reconnect, 'connection_group', group),
                      patch.object(reconnect, 'disconnect_existing', side_effect=disconnect),
                      patch.object(reconnect, 'remove_exception') as cleanup,
                      patch.object(reconnect.subprocess, 'run', side_effect=launch) as run):
                    if failure:
                        with self.assertRaisesRegex(CliError, 'Cannot disconnect'):
                            reconnect.protected_connect(Selection(country='DE'), account, runtime=runtime, replace_existing=True)
                        run.assert_not_called()
                        enable.assert_not_called()
                    else:
                        result = reconnect.protected_connect(Selection(country='DE'), account, runtime=runtime, replace_existing=True)
                        self.assertIn('Disconnected', result)
                        self.assertEqual(enable.call_args.args[0], 'enable')
                    cleanup.assert_called_once_with(guard, 'cyberghost-desktop-connect-0123')
                    guard.disable.assert_not_called()
                    self.assertFalse(list(runtime.glob('connect-*')))

    def test_unknown_guard_never_launches_connection(self):
        with tempfile.TemporaryDirectory() as directory:
            with (patch.object(reconnect, 'system_tool', return_value='/unused'),
                  patch.object(reconnect, 'direct_resolvers', return_value='nameserver 192.0.2.53\n'),
                  patch.object(reconnect.firewall, 'secure_runtime'),
                  patch.object(reconnect.firewall, 'request', return_value=GuardStatus('unknown', 'Cannot verify')),
                  patch.object(reconnect, 'connection_group') as group,
                  patch.object(reconnect.subprocess, 'run') as run,
                  self.assertRaisesRegex(RuntimeError, 'could not be verified')):
                reconnect.protected_connect(Selection(country='DE'), SimpleNamespace(), runtime=Path(directory))
            group.assert_not_called()
            run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
