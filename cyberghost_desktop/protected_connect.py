"""One authorized WireGuard reconnect without lifting the traffic block.

The CLI and its children get a temporary, root-owned cgroup exception. A private
mount namespace makes their DNS requests go directly to the configured uplink
resolver, instead of allowing every application's system DNS traffic to escape.
"""

from contextlib import contextmanager
import fcntl
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import pwd
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid

if __package__:
    from . import cli, firewall
else:
    # -I deliberately excludes the checkout from sys.path. Load only our two
    # stdlib-only siblings, never modules found through an inherited PYTHONPATH.
    def sibling(name):
        spec = importlib.util.spec_from_file_location('_cyberghost_connect_' + name, Path(__file__).with_name(name + '.py'))
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    cli, firewall = sibling('cli'), sibling('firewall')

CGROUP_ROOT = Path('/sys/fs/cgroup')
ENV = {'PATH': firewall.SYSTEM_PATH, 'LC_ALL': 'C', 'LANG': 'C'}
DNS_FILES = (Path('/run/systemd/resolve/resolv.conf'), Path('/run/NetworkManager/no-stub-resolv.conf'),
             Path('/run/NetworkManager/resolv.conf'), Path('/etc/resolv.conf'))


def connection_rule(group):
    parts = group.split('/')
    if not parts or any(not re.fullmatch(r'[A-Za-z0-9_.:@-]+', part) or part in {'.', '..'} for part in parts):
        raise ValueError('Invalid connection cgroup.')
    return (f'insert rule inet {firewall.TABLE} output meta skuid 0 '
            f'socket cgroupv2 level {len(parts)} "{group}" accept comment "{group}"\n')


def remove_exception(guard, group):
    document = guard.document()
    if document is None:
        raise RuntimeError('VPN-only rules disappeared during connection setup. Refresh to verify protection.')
    handles = [entry['rule']['handle'] for entry in document['nftables']
               if entry.get('rule', {}).get('chain') == 'output' and entry['rule'].get('comment') == group]
    if handles:
        source = ''.join(f'delete rule inet {firewall.TABLE} output handle {int(handle)}\n' for handle in handles)
        guard.command('nft', '--file', '-', source=source)


def direct_resolvers(paths=DNS_FILES):
    for path in paths:
        try:
            lines = path.read_text().splitlines()
        except OSError:
            continue
        servers = []
        for line in lines:
            fields = line.split('#', 1)[0].split()
            if len(fields) < 2 or fields[0] != 'nameserver':
                continue
            try:
                address = ipaddress.ip_address(fields[1])
            except ValueError:
                continue
            if not (address.is_loopback or address.is_unspecified or address.is_multicast) and str(address) not in servers:
                servers.append(str(address))
        if servers:
            return ''.join(f'nameserver {address}\n' for address in servers[:3]) + 'options timeout:2 attempts:2\n'
    raise RuntimeError('No uplink DNS servers are available for a protected reconnect. VPN-only protection remains enabled.')


@contextmanager
def connection_group(root=CGROUP_ROOT):
    if not (root / 'cgroup.controllers').is_file():
        raise RuntimeError('Protected reconnect requires cgroup v2. VPN-only protection remains enabled.')
    path = root / ('cyberghost-desktop-connect-' + uuid.uuid4().hex)
    try:
        path.mkdir(mode=0o700)
    except OSError as error:
        raise RuntimeError('Could not isolate CyberGhost for a protected reconnect. VPN-only protection remains enabled.') from error
    try:
        yield path
    finally:
        # Only this helper's child processes can enter the root-owned group.
        # A failed/finished CLI must not leave an authorized child running.
        if (path / 'cgroup.procs').read_text().strip():
            kill = path / 'cgroup.kill'
            if kill.exists():
                kill.write_text('1')
            else:
                for pid in (path / 'cgroup.procs').read_text().split():
                    try:
                        os.kill(int(pid), 9)
                    except ProcessLookupError:
                        pass
            deadline = time.monotonic() + 3
            while (path / 'cgroup.procs').read_text().strip() and time.monotonic() < deadline:
                time.sleep(.02)
        path.rmdir()


def system_tool(name):
    result = shutil.which(name, path=firewall.SYSTEM_PATH)
    if not result:
        raise RuntimeError(f'Install {name} for protected reconnects. VPN-only protection remains enabled.')
    return result


def account_environment(account):
    return {**ENV, 'SUDO_USER': account.pw_name, 'HOME': account.pw_dir, 'USER': 'root', 'LOGNAME': 'root'}


def disconnect_existing(binary, account):
    env = account_environment(account)
    def invoke(command, **kwargs):
        kwargs['env'] = env
        return subprocess.run(command, **kwargs)
    client = cli.CyberGhostClient(binary, run=invoke)
    state = client.status().state
    if state == 'disconnected':
        return ''
    if state != 'connected':
        raise RuntimeError('Could not verify the existing VPN. Refresh before switching servers.')
    result = invoke([binary, '--stop'], stdin=subprocess.DEVNULL, capture_output=True, text=True,
                    errors='replace', check=False)
    output = cli.clean_output(result.stdout + '\n' + result.stderr)
    if result.returncode or client.status().state != 'disconnected':
        raise cli.CliError('The previous VPN could not be disconnected. The new connection was not started.', output)
    return output


def switch_unprotected(selection, account):
    args = selection.connect_args()  # Validate before disconnecting anything.
    binary = system_tool('cyberghostvpn')
    if shutil.which('nft', path=firewall.SYSTEM_PATH) and firewall.Firewall().document() is not None:
        raise RuntimeError('VPN-only protection changed. Refresh before switching servers.')
    previous = disconnect_existing(binary, account)
    result = subprocess.run([binary, *args], stdin=subprocess.DEVNULL, capture_output=True, text=True,
                            errors='replace', env=account_environment(account), check=False)
    output = cli.clean_output(previous + '\n' + result.stdout + '\n' + result.stderr)
    if result.returncode:
        raise cli.CliError('The new VPN connection could not be established.', output)
    return output


def protected_connect(selection, account, *, runtime=firewall.RUNTIME, replace_existing=False):
    args = selection.connect_args()
    if selection.protocol != 'wireguard':
        raise ValueError('VPN-only reconnect requires WireGuard.')
    binary = system_tool('cyberghostvpn')
    unshare = system_tool('unshare')
    system_tool('mount')
    system_tool('nsenter')
    resolvers = direct_resolvers()
    firewall.secure_runtime(runtime)
    descriptor = os.open(runtime / 'action.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if firewall.request('status', runtime).state != 'on':
            raise RuntimeError('VPN-only protection could not be verified. Refresh its status before reconnecting.')
        guard = firewall.Firewall()
        if guard.document() is None:
            raise RuntimeError('VPN-only rules are missing; connection was not started.')
        with connection_group() as group, tempfile.TemporaryDirectory(prefix='connect-', dir=runtime) as temporary:
            job = Path(temporary)
            config = {'group': group.name, 'parent': os.getpid(), 'binary': binary, 'args': args,
                      'username': account.pw_name, 'home': account.pw_dir}
            (job / 'job.json').write_text(json.dumps(config))
            (job / 'resolv.conf').write_text(resolvers)
            # Never replace the drop policy. The exception matches only sockets
            # of this CLI's root-owned cgroup, for both IPv4 and IPv6.
            guard.command('nft', '--file', '-', source=connection_rule(group.name))
            try:
                previous = disconnect_existing(binary, account) if replace_existing else ''
                if replace_existing:
                    # Disconnect may remove the old tunnel's DNS servers.
                    (job / 'resolv.conf').write_text(direct_resolvers())
                command = [unshare, '--mount', '--propagation', 'private', '--', '/usr/bin/python3', '-I',
                           str(Path(__file__).resolve()), '--worker', str(job)]
                result = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                        errors='replace', env=ENV, check=False)
                output = cli.clean_output(previous + '\n' + result.stdout + '\n' + result.stderr)
                if result.returncode:
                    raise cli.CliError('CyberGhost could not reconnect. VPN-only protection remains enabled.', output)
                # CyberGhost 1.5 can report a connection failure and still
                # exit zero. Retain its output when the tunnel check fails;
                # the guard's activation advice is not the connection error.
                try:
                    guard.wireguard_mark()
                except RuntimeError as error:
                    raise cli.CliError('CyberGhost did not establish a usable WireGuard connection. '
                                       'VPN-only protection remains enabled.',
                                       output or 'The CLI exited without creating an active WireGuard tunnel and route.') from error
                # This also works with the old running firewall monitor.
                try:
                    status = firewall.authorized_action('enable', runtime, _lock=lock.fileno())
                    if status.state != 'on':
                        raise RuntimeError('The new VPN-only rules could not be verified.')
                except RuntimeError as error:
                    raise cli.CliError('The WireGuard connection started, but VPN-only protection could not be reverified.',
                                       output + '\n' + str(error)) from error
                return output + '\nVPN-only protection remained enabled during reconnect.'
            finally:
                remove_exception(guard, group.name)


def mount_file(source, target, *, executable=False):
    mount = system_tool('mount')
    commands = [[mount, '--bind', str(source), str(target)]]
    if executable:
        # A bind inherits the source mount's noexec flag (/run normally has
        # it). Allow this fixed DNS wrapper only at its private bind target.
        # The runtime filesystem and host executable retain their own flags.
        commands.append([mount, '-o', 'remount,bind,ro,exec,nosuid,nodev', str(target)])
    for command in commands:
        result = subprocess.run(command, capture_output=True, text=True, env=ENV, check=False)
        if result.returncode:
            raise RuntimeError('Could not prepare private DNS for CyberGhost. VPN-only protection remains enabled.')


def worker(job):
    job = Path(job).resolve(strict=True)
    if job.parent != firewall.RUNTIME or not job.name.startswith('connect-'):
        raise ValueError('Invalid connection workspace.')
    info = job.stat()
    if info.st_uid != 0 or info.st_mode & 0o077 or not stat.S_ISDIR(info.st_mode):
        raise ValueError('The connection workspace must be private and root-owned.')
    config = json.loads((job / 'job.json').read_text())
    if not re.fullmatch(r'cyberghost-desktop-connect-[0-9a-f]{32}', config['group']):
        raise ValueError('Invalid connection group.')
    # Fail before mounting anything if this is accidentally invoked outside the
    # child namespace. The host resolver and NSS configuration stay unchanged.
    host_namespace = f'/proc/{int(config["parent"])}/ns/mnt'
    if os.readlink('/proc/self/ns/mnt') == os.readlink(host_namespace):
        raise RuntimeError('Private connection namespace was not created.')
    (CGROUP_ROOT / config['group'] / 'cgroup.procs').write_text(str(os.getpid()))
    nss = Path('/etc/nsswitch.conf')
    lines = [line for line in nss.read_text().splitlines() if not re.match(r'^\s*hosts\s*:', line)]
    (job / 'nsswitch.conf').write_text('\n'.join(lines) + '\nhosts: files dns\n')
    mount_file(job / 'nsswitch.conf', nss)
    mount_file(job / 'resolv.conf', Path('/etc/resolv.conf').resolve())
    # nscd can otherwise bypass the private resolver through its shared socket.
    nscd = Path('/run/nscd/socket')
    if nscd.exists():
        (job / 'no-nscd').touch()
        mount_file(job / 'no-nscd', nscd)
    resolvconf = shutil.which('resolvconf', path=firewall.SYSTEM_PATH)
    if resolvconf:
        # wg-quick must update the host's DNS. Interpose the executable in this
        # namespace, including absolute calls and wg-quick's own PATH changes.
        # systemd uses one binary for both resolvconf and resolvectl; preserve
        # the invoked name so each still receives the appropriate arguments.
        prefix = [system_tool('nsenter'), '--mount=' + host_namespace, '--']
        commands = {'resolvconf': resolvconf}
        if Path(resolvconf).resolve().name == 'resolvectl':
            commands['resolvectl'] = system_tool('resolvectl')
        wrapper = job / 'dns-update'
        script = '#!/bin/sh\nunset LD_LIBRARY_PATH LD_PRELOAD LD_AUDIT\ncase "${0##*/}" in\n'
        for name, binary in commands.items():
            script += name + ') exec ' + shlex.join(prefix + [binary]) + ' "$@" ;;\n'
        script += '*) exit 1 ;;\nesac\n'
        wrapper.write_text(script)
        wrapper.chmod(0o700)
        mount_file(wrapper, Path(resolvconf).resolve(), executable=True)
    env = {**ENV, 'SUDO_USER': config['username'],
           'HOME': config['home'], 'USER': 'root', 'LOGNAME': 'root'}
    os.execve(config['binary'], [config['binary'], *config['args']], env)


def main():
    try:
        if os.geteuid() != 0:
            raise RuntimeError('Protected reconnect requires administrator authorization.')
        if len(sys.argv) == 3 and sys.argv[1] == '--worker':
            worker(sys.argv[2])
            return 0
        if len(sys.argv) != 2 or len(sys.argv[1]) > 4096:
            raise ValueError('Expected a WireGuard connection selection.')
        uid = int(os.environ.get('PKEXEC_UID', '0'))
        if uid <= 0:
            raise ValueError('Open CyberGhost Desktop from your normal desktop account.')
        data = json.loads(sys.argv[1])
        # Flat selections remain accepted for an app already running during
        # an update. New clients describe protection and server switching.
        options = data if 'selection' in data else {'selection': data, 'vpn_only': True, 'replace_existing': False}
        vpn_only, replace_existing = options.get('vpn_only'), options.get('replace_existing')
        if type(vpn_only) is not bool or type(replace_existing) is not bool:
            raise ValueError('Invalid connection operation.')
        selection = cli.Selection(**options['selection'])
        account = pwd.getpwuid(uid)
        if vpn_only:
            output = protected_connect(selection, account, replace_existing=replace_existing)
        elif replace_existing:
            output = switch_unprotected(selection, account)
        else:
            raise ValueError('Expected a protected connection or server switch.')
        print(output)
        return 0
    except Exception as error:
        print(str(error), file=sys.stderr)
        details = getattr(error, 'details', '')
        if details:
            print(details, file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
