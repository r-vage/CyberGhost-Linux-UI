"""Optional, narrowly scoped polkit authorization retention for VPN controls."""

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import shutil
import stat
import subprocess

ACTION = 'io.github.rvage.CyberGhostDesktop.control'
MARKER = 'cyberghost-desktop managed authorization'
LIBRARY = Path('/usr/local/libexec/cyberghost-desktop/control')
POLICY = Path('/usr/share/polkit-1/actions') / (ACTION + '.policy')
COPIES = ('authorization_helper.py', 'protected_connect.py', 'cli.py', 'firewall.py', 'startup_system.py')
SYSTEM_PATH = '/usr/sbin:/usr/bin:/sbin:/bin'


@dataclass(frozen=True)
class AuthorizationStatus:
    state: str
    detail: str


def source_hashes(source):
    return {name: hashlib.sha256((source / name).read_text().encode('utf-8')).hexdigest() for name in COPIES}


def trusted(path):
    if path.is_symlink():
        raise ValueError('Authorization files must not be symlinks.')
    for item in (path, *path.parents):
        info = item.stat()
        if info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('Authorization files must be root-owned and not writable by other users.')


def status(*, library=LIBRARY, policy=POLICY, source=None):
    source = source or Path(__file__).parent
    if not library.exists() and not policy.exists():
        return AuthorizationStatus('off', 'Linux asks for authorization for each VPN change.')
    try:
        for path in (library / '.manifest.json', library / 'command', policy, *(library / name for name in COPIES)):
            trusted(path)
            if not stat.S_ISREG(path.stat().st_mode):
                raise ValueError('Authorization helper files must be regular files.')
        manifest = json.loads((library / '.manifest.json').read_text())
        if not isinstance(manifest, dict) or manifest.get('managed_by') != MARKER or MARKER not in policy.read_text():
            raise ValueError('Unrecognized authorization installation.')
        expected = source_hashes(source)
        if manifest.get('files') != expected or source_hashes(library) != expected:
            return AuthorizationStatus('outdated', 'Authorization helper needs an update. Until then, Linux asks for each change.')
        return AuthorizationStatus('ready', 'Linux can remember authorization briefly, typically about five minutes.')
    except (OSError, ValueError) as error:
        return AuthorizationStatus('unavailable', f'Authorization helper is unavailable: {error}')


def control_command(pkexec, action, payload):
    if status().state != 'ready':
        return None
    return [pkexec, '--disable-internal-agent', str(LIBRARY / 'command'), action, json.dumps(payload)]


def configure(enabled):
    pkexec = shutil.which('pkexec', path=SYSTEM_PATH)
    if not pkexec:
        raise RuntimeError('Install polkit (pkexec) to configure authorization caching.')
    helper = Path(__file__).with_name('authorization_install.py').resolve()
    if not helper.is_file():
        raise RuntimeError('Authorization installer is missing. Quit and reopen the app after moving it.')
    result = subprocess.run([pkexec, '--disable-internal-agent', '/usr/bin/python3', '-I', str(helper),
                             'install' if enabled else 'remove'], stdin=subprocess.DEVNULL,
                            capture_output=True, text=True, check=False)
    if result.returncode in {126, 127}:
        raise RuntimeError('Authorization setup was cancelled or denied.')
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or 'Could not configure authorization caching.')
    return status()
