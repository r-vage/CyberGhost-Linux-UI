"""Installed, root-owned entry point for the app's cacheable polkit action."""

import importlib.util
import json
import os
from pathlib import Path
import pwd
import subprocess
import sys

LIBRARY = Path('/usr/local/libexec/cyberghost-desktop/control')


def sibling(name):
    spec = importlib.util.spec_from_file_location('_cyberghost_authorized_' + name, Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def dispatch(action, payload, account, connection, firewall, trust):
    if action == 'connect':
        if not isinstance(payload, dict) or set(payload) != {'selection', 'vpn_only', 'replace_existing'}:
            raise ValueError('Invalid connection request.')
        if type(payload['vpn_only']) is not bool or type(payload['replace_existing']) is not bool:
            raise ValueError('Invalid connection options.')
        selection = connection.cli.Selection(**payload['selection'])
        args = selection.connect_args()
        binary = str(trust(Path(connection.system_tool('cyberghostvpn'))))
        if payload['vpn_only']:
            return connection.protected_connect(selection, account, replace_existing=payload['replace_existing'])
        if payload['replace_existing']:
            return connection.switch_unprotected(selection, account)
        if connection.shutil.which('nft', path=firewall.SYSTEM_PATH) and firewall.Firewall().document() is not None:
            raise RuntimeError('VPN-only protection changed. Refresh before connecting.')
        result = subprocess.run([binary, *args], stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                errors='replace', env=connection.account_environment(account), check=False)
        output = connection.cli.clean_output(result.stdout + '\n' + result.stderr)
        if result.returncode:
            raise connection.cli.CliError('CyberGhost could not connect.', output)
        return output
    if action == 'disconnect' and payload == {}:
        binary = str(trust(Path(connection.system_tool('cyberghostvpn'))))
        result = subprocess.run([binary, '--stop'], stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                errors='replace', env=connection.account_environment(account), check=False)
        output = connection.cli.clean_output(result.stdout + '\n' + result.stderr)
        if result.returncode:
            raise connection.cli.CliError('CyberGhost could not disconnect.', output)
        return output
    if action == 'guard' and isinstance(payload, dict) and set(payload) == {'enabled'} and type(payload['enabled']) is bool:
        from dataclasses import asdict
        return json.dumps(asdict(firewall.authorized_action('enable' if payload['enabled'] else 'disable')))
    raise ValueError('Only VPN connect, disconnect and VPN-only changes are supported.')


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else ''
    try:
        if os.geteuid() != 0 or Path(__file__).resolve() != LIBRARY / 'authorization_helper.py':
            raise RuntimeError('Use the installed authorization helper through polkit.')
        if len(sys.argv) != 3 or len(sys.argv[2]) > 4096:
            raise ValueError('Invalid VPN control request.')
        uid = int(os.environ.get('PKEXEC_UID', '0'))
        if uid <= 0:
            raise ValueError('Use your normal desktop account to authorize VPN changes.')
        system = sibling('startup_system')
        for name in ('authorization_helper.py', 'startup_system.py', 'protected_connect.py', 'cli.py', 'firewall.py'):
            system.trusted(LIBRARY / name)
        connection, firewall = sibling('protected_connect'), sibling('firewall')
        result = dispatch(action, json.loads(sys.argv[2]), pwd.getpwuid(uid), connection, firewall, system.trusted)
        print(result)
        return 0
    except Exception as error:
        details = getattr(error, 'details', '')
        message = str(error) + ('\n' + details if details else '')
        if action == 'guard':
            print(json.dumps({'error': message}))
        else:
            print(message, file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
