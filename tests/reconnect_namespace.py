"""Test reconnect DNS isolation in fresh user, network and mount namespaces.

The CyberGhost executable and DNS updater are inert fixtures. wg-quick creates
a test tunnel to an unreachable dummy peer in the isolated network namespace.
This script never connects a real VPN, queries DNS, changes host mounts, or writes
host firewall rules.
"""

from contextlib import ExitStack
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cyberghost_desktop import protected_connect as reconnect  # noqa: E402


def bind(source, target):
    subprocess.run(['mount', '--bind', str(source), str(target)], check=True)


def inner(original_mount, original_network):
    assert os.readlink('/proc/self/ns/mnt') != original_mount
    assert os.readlink('/proc/self/ns/net') != original_network
    assert os.geteuid() == 0
    resolver = shutil.which('resolvconf')
    assert resolver, 'This integration check needs resolvconf (or its systemd symlink)'
    with tempfile.TemporaryDirectory(prefix='cg-private-dns-') as directory, ExitStack() as mounts:
        root = Path(directory)
        runtime = root / 'runtime'
        runtime.mkdir()
        # /run is normally noexec. The temporary wrapper must work even when
        # its source file is on a non-executable runtime filesystem.
        subprocess.run(['mount', '-t', 'tmpfs', '-o', 'mode=755,nosuid,nodev,noexec', 'tmpfs', str(runtime)], check=True)
        mounts.callback(subprocess.run, ['umount', str(runtime)], check=True)
        assert os.statvfs(runtime).f_flag & os.ST_NOEXEC
        job = runtime / 'connect-test'
        job.mkdir(mode=0o700)
        groups = root / 'groups'
        group = groups / ('cyberghost-desktop-connect-' + '0' * 32)
        group.mkdir(parents=True)
        (group / 'cgroup.procs').touch()
        # Use the real wg-quick startup and route checks as well as the DNS
        # fixture. The dummy uplink has no access to the host or internet.
        for args in (['ip', 'link', 'set', 'lo', 'up'],
                     ['ip', 'link', 'add', 'plain0', 'type', 'dummy'],
                     ['ip', 'addr', 'add', '198.18.0.1/24', 'dev', 'plain0'],
                     ['ip', 'link', 'set', 'plain0', 'up'],
                     ['ip', 'route', 'add', 'default', 'dev', 'plain0']):
            subprocess.run(args, check=True)
        wgdir = root / 'wireguard'
        wgdir.mkdir(mode=0o700)
        key = subprocess.check_output(['wg', 'genkey'], text=True).strip()
        peer_key = subprocess.check_output(['wg', 'genkey'], text=True).strip()
        public = subprocess.check_output(['wg', 'pubkey'], input=peer_key, text=True).strip()
        config_text = (f'[Interface]\nAddress = 10.22.0.1/32\nPrivateKey = {key}\n'
                       'PostUp = resolvectl dns %i 192.0.2.53; resolvectl domain %i ~.; resolvectl default-route %i true\n'
                       'PostDown = resolvectl revert %i\n'
                       f'[Peer]\nPublicKey = {public}\nAllowedIPs = 0.0.0.0/0\nEndpoint = 198.18.0.2:51820\n')
        config_path = wgdir / 'cyberghost.conf'
        config_path.write_text(config_text)
        config_path.chmod(0o600)
        bind(wgdir, '/etc/wireguard')
        parent_ns = os.readlink('/proc/self/ns/mnt')
        host_dns = root / 'host-resolv.conf'
        host_dns.write_text('nameserver 127.0.0.53\n')
        bind(host_dns, Path('/etc/resolv.conf').resolve())
        nss = root / 'host-nsswitch.conf'
        original_nss = Path('/etc/nsswitch.conf').read_text()
        nss.write_text(original_nss)
        bind(nss, '/etc/nsswitch.conf')
        record = root / 'dns-updates.jsonl'
        fake_update = root / 'fake-dns-update'
        fake_update.write_text('#!/usr/bin/python3\n' + f'''
import json, os, pathlib, sys
assert os.readlink('/proc/self/ns/mnt') == {parent_ns!r}, 'DNS update must run in the parent mount namespace'
assert pathlib.Path('/etc/resolv.conf').read_text() == 'nameserver 127.0.0.53\\n'
with open({str(record)!r}, 'a') as stream:
    stream.write(json.dumps(sys.argv) + '\\n')
''')
        fake_update.chmod(0o700)
        bind(fake_update, Path(resolver).resolve())
        private_dns = 'nameserver 192.0.2.53\n'
        (job / 'resolv.conf').write_text(private_dns)
        fake_cli = root / 'fake-cyberghost'
        aliases = [resolver]
        if Path(resolver).resolve().name == 'resolvectl':
            aliases.append(shutil.which('resolvectl'))
        fake_cli.write_text('#!/usr/bin/python3\n' + f'''
import os, pathlib, subprocess
assert os.readlink('/proc/self/ns/mnt') != {parent_ns!r}
assert pathlib.Path('/etc/resolv.conf').read_text() == {private_dns!r}
assert [line for line in pathlib.Path('/etc/nsswitch.conf').read_text().splitlines() if line.startswith('hosts:')] == ['hosts: files dns']
assert os.environ['SUDO_USER'] == 'test-user'
for command in {aliases!r}:
    subprocess.run([command, 'fixture-only'], check=True)
result = subprocess.run(['wg-quick', 'up', 'cyberghost'], capture_output=True, text=True)
assert result.returncode == 0, result.stderr
print('Private DNS, host DNS update and isolated WireGuard startup checks passed.')
''')
        fake_cli.chmod(0o700)
        config = {'group': group.name, 'parent': os.getpid(), 'binary': str(fake_cli), 'args': [],
                  'username': 'test-user', 'home': str(root)}
        (job / 'job.json').write_text(json.dumps(config))
        subprocess.run(['unshare', '--mount', '--propagation', 'private', '--', '/usr/bin/python3', __file__,
                        '--worker', str(job), str(groups)], check=True)
        assert Path('/etc/resolv.conf').read_text() == 'nameserver 127.0.0.53\n'
        assert Path('/etc/nsswitch.conf').read_text() == original_nss
        updates = [json.loads(line) for line in record.read_text().splitlines()]
        assert [entry[0] for entry in updates[:len(aliases)]] == aliases
        assert all(entry[1:] == ['fixture-only'] for entry in updates[:len(aliases)])
        assert [entry[1:] for entry in updates[len(aliases):]] == [
            ['dns', 'cyberghost', '192.0.2.53'], ['domain', 'cyberghost', '~.'], ['default-route', 'cyberghost', 'true']]
        assert reconnect.firewall.Firewall().wireguard_mark() == 51820
        assert os.statvfs(job / 'dns-update').f_flag & os.ST_NOEXEC, 'Runtime source must remain noexec'
        assert Path(resolver).resolve().read_bytes() == fake_update.read_bytes(), 'Host DNS executable must remain unchanged'

        assert fake_update.read_text().startswith('#!/usr/bin/python3'), 'Original DNS updater was not replaced'


if __name__ == '__main__':
    if len(sys.argv) == 1:
        raise SystemExit(subprocess.call(['unshare', '--user', '--map-root-user', '--net', '--mount', '--propagation', 'private',
                                         '/usr/bin/python3', __file__, '--inside', os.readlink('/proc/self/ns/mnt'), os.readlink('/proc/self/ns/net')]))
    if len(sys.argv) == 4 and sys.argv[1] == '--inside':
        inner(sys.argv[2], sys.argv[3])
    elif len(sys.argv) == 4 and sys.argv[1] == '--worker':
        reconnect.firewall.RUNTIME = Path(sys.argv[2]).parent
        reconnect.CGROUP_ROOT = Path(sys.argv[3])
        reconnect.worker(sys.argv[2])
    else:
        raise SystemExit('Run without arguments to create isolated test namespaces.')
