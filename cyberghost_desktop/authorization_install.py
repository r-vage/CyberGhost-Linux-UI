"""Explicit setup/removal of the app-specific polkit action and helper snapshot.

This installer always uses the generic, non-cached administrator authorization;
installing code or changing policy is never part of the cacheable VPN action.
"""

import importlib.util
import json
import os
from pathlib import Path
import sys

if __package__:
    from . import authorization as settings, startup_system as system
else:
    def sibling(name):
        spec = importlib.util.spec_from_file_location('_cyberghost_authorization_install_' + name, Path(__file__).with_name(name + '.py'))
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    settings, system = sibling('authorization'), sibling('startup_system')


def policy_text():
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<!-- {settings.MARKER} -->
<policyconfig>
  <vendor>CyberGhost-Linux-UI</vendor>
  <action id="{settings.ACTION}">
    <description>Control CyberGhost VPN</description>
    <message>Authentication is required to change your CyberGhost VPN connection or VPN-only protection</message>
    <defaults>
      <allow_any>auth_admin</allow_any>
      <allow_inactive>auth_admin</allow_inactive>
      <allow_active>auth_admin_keep</allow_active>
    </defaults>
    <annotate key="org.freedesktop.policykit.exec.path">{settings.LIBRARY}/command</annotate>
  </action>
</policyconfig>
'''


def configure(enabled, *, source=None, library=settings.LIBRARY, policy=settings.POLICY):
    source = source or Path(__file__).resolve().parent
    if policy.is_symlink() or (policy.exists() and settings.MARKER not in policy.read_text()):
        raise RuntimeError('Refusing to replace an unrelated polkit policy.')
    manifest = library / '.manifest.json'
    if library.exists():
        system.trusted(library)
        data = json.loads(manifest.read_text()) if manifest.is_file() else None
        if not isinstance(data, dict) or data.get('managed_by') != settings.MARKER:
            raise RuntimeError('Refusing to change an unrelated helper directory.')
    if not enabled:
        # Removing the executable also makes any still-cached polkit grant
        # unusable; never change sudoers or other applications' policies.
        policy.unlink(missing_ok=True)
        for name in (*settings.COPIES, 'command', '.manifest.json'):
            (library / name).unlink(missing_ok=True)
        if library.exists():
            library.rmdir()
        return
    contents = {name: (source / name).read_text() for name in settings.COPIES}
    hashes = settings.source_hashes(source)
    launcher = f'''#!/bin/sh
# {settings.MARKER}
exec /usr/bin/python3 -I {settings.LIBRARY}/authorization_helper.py "$@"
'''
    targets = {**{library / name: (text, 0o644) for name, text in contents.items()},
               library / 'command': (launcher, 0o755),
               manifest: (json.dumps({'managed_by': settings.MARKER, 'files': hashes}, indent=2) + '\n', 0o644),
               policy: (policy_text(), 0o644)}
    old = {}
    for path in targets:
        if path.is_symlink():
            raise RuntimeError('Refusing to replace a symlink in the authorization installation.')
        old[path] = (path.read_text(), path.stat().st_mode & 0o777) if path.exists() else None
    try:
        for path, (text, mode) in targets.items():
            system.write(path, text, mode)
    except Exception:
        for path, previous in reversed(list(old.items())):
            if previous is None:
                path.unlink(missing_ok=True)
            else:
                system.write(path, *previous)
        if library.exists() and not any(library.iterdir()):
            library.rmdir()
        raise


def main():
    try:
        if os.geteuid() != 0 or len(sys.argv) != 2 or sys.argv[1] not in {'install', 'remove'}:
            raise RuntimeError('Authorize installation or removal through Setup.')
        uid = int(os.environ.get('PKEXEC_UID', '0'))
        if uid <= 0:
            raise RuntimeError('Use Setup from your normal desktop account.')
        configure(sys.argv[1] == 'install')
        print('Authorization caching enabled.' if sys.argv[1] == 'install' else 'Authorization caching removed.')
        return 0
    except Exception as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
