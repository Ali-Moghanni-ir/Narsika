#!/usr/bin/env python3
"""Read-only native-install diagnostics; never print configuration values or secrets."""
import os
from pathlib import Path
import subprocess
import urllib.request


def inspect(base=Path('/opt/narsika'), data=Path('/var/lib/narsika'),
            config=Path('/etc/narsika/narsika.env')):
    checks=[]
    def record(name, passed, detail):
        checks.append(dict(name=name, status='PASS' if passed else 'FAIL', detail=detail))
    def command(name, args, extra_env=None):
        try:
            result=subprocess.run([str(a) for a in args], capture_output=True, text=True,
                                  timeout=90, env={**os.environ, 'LC_ALL':'C', **(extra_env or {})})
            record(name, result.returncode==0, 'Command completed.' if result.returncode==0
                   else f'Exit {result.returncode}; inspect this component locally.')
        except (OSError, subprocess.TimeoutExpired):
            record(name, False, 'Command unavailable or timed out.')
    current=base/'current'
    try:
        valid_release=current.is_symlink() and current.resolve(strict=True).is_relative_to(base/'releases')
    except OSError:
        valid_release=False
    record('Release pointer', valid_release, 'Expected a valid installer-managed release.')
    values={}
    try:
        values=dict(line.split('=',1) for line in config.read_text().splitlines()
                    if line and not line.startswith('#') and '=' in line)
        valid=bool(values.get('NARSIKA_SECRET_KEY') and values.get('NARSIKA_ENCRYPTION_KEY'))
        private=not config.stat().st_mode & 0o007
        record('Configuration and keys', valid and private,
               'Key presence and world-access permissions checked; values are never displayed.')
    except OSError:
        record('Configuration and keys', False, 'Missing or unreadable configuration; restore original keys, do not regenerate.')
    record('Database', (data/'narsika.db').is_file(), 'Existing database presence; no database writes performed.')
    command('systemd service', ['systemctl','is-active','--quiet','narsika'])
    python=current/'.venv/bin/python'
    if valid_release and python.is_file():
        command('Python runtime', [python,'-c','import sys; raise SystemExit(not (3, 12) <= sys.version_info < (3, 15))'])
        command('Python dependency consistency', [python,'-m','pip','check'])
        command('Ansible executable', [current/'.venv/bin/ansible-playbook','--version'],
                {'ANSIBLE_COLLECTIONS_PATH':str(current.resolve()/'.venv/collections'),
                 'ANSIBLE_COLLECTIONS_SCAN_SYS_PATH':'False',
                 'ANSIBLE_CONFIG':str(current.resolve()/'ansible.cfg')})
        command('Isolated Ansible runtime and collections', [python,current/'tools/install_collections.py','--verify-only'])
    else:
        record('Python runtime', False, 'Virtual environment is missing.')
    for namespace,name in [('ansible','netcommon'),('ansible','utils'),('cisco','ios'),('community','routeros')]:
        record('Collection '+namespace+'.'+name,
               (current/'.venv/collections/ansible_collections'/namespace/name/'MANIFEST.json').is_file(),
               'Local collection manifest presence only.')
    try:
        port=int(values.get('NARSIKA_PORT','8000'))
        if not 1024<=port<=65535:raise ValueError('Invalid port')
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f'http://127.0.0.1:{port}/healthz',timeout=3) as response:
            record('Loopback HTTP health', response.status==200, 'Local endpoint; not a LAN reachability test.')
    except (OSError,ValueError):
        record('Loopback HTTP health', False, 'No healthy local HTTP response on the configured port.')
    return checks


def main():
    checks=inspect()
    for check in checks:print(check['status']+' | '+check['name']+' | '+check['detail'])
    print('Read-only checks. No service restart, package install, password reset or firewall modification.')
    print('Device connectivity, UFW policy and remote client reachability require separate lab checks.')
    return int(any(check['status']=='FAIL' for check in checks))


if __name__=='__main__':raise SystemExit(main())
