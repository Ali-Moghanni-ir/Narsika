#!/usr/bin/env python3
"""Local administrator operations. No backup schedule or automatic retention."""
import argparse
import fcntl
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import tarfile
import tempfile
import time
from contextlib import contextmanager

BASE = Path('/opt/narsika')
DATA = Path('/var/lib/narsika')
CONFIG = Path('/etc/narsika/narsika.env')


def run(*args, **kwargs):
    return subprocess.run([str(a) for a in args], check=True, **kwargs)


@contextmanager
def stopped():
    active = subprocess.run(['systemctl', 'is-active', '--quiet', 'narsika']).returncode == 0
    if active:
        run('systemctl', 'stop', 'narsika')
    try:
        yield
    finally:
        if active:
            run('systemctl', 'start', 'narsika')


def backup():
    destination = Path('/var/backups/narsika')
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    archive = destination / (time.strftime('narsika-%Y%m%d-%H%M%S-') + secrets.token_hex(4) + '.tar.gz')
    with stopped(), (DATA / 'worker.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise SystemExit('Another Narsika process is using the data; stop it before backup.') from None
        if not CONFIG.is_file() or not (DATA / 'narsika.db').is_file():
            raise SystemExit('No initialized native installation exists.')
        with tempfile.TemporaryDirectory(prefix='narsika-backup-') as temporary:
            snapshot = Path(temporary) / 'narsika.db'
            run('/usr/bin/python3', BASE / 'current/tools/snapshot.py', DATA / 'narsika.db', snapshot)
            fd = os.open(archive, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as stream, tarfile.open(fileobj=stream, mode='w:gz') as output:
                output.add(CONFIG, arcname='etc/narsika/narsika.env', recursive=False)
                output.add(snapshot, arcname='var/lib/narsika/narsika.db', recursive=False)
                for name in ('backups', 'playbooks', 'known_hosts', 'archives'):
                    source = DATA / name
                    if source.exists():
                        output.add(source, arcname='var/lib/narsika/' + name)
                for source in DATA.glob('narsika.db.before-*.enc'):
                    output.add(source, arcname='var/lib/narsika/' + source.name, recursive=False)
    print('Private backup created: ' + str(archive))
    print('Contains encryption keys. Keep it private. No scheduled backup or retention was enabled.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('status', 'start', 'stop', 'restart', 'logs', 'backup'):
        sub.add_parser(name)
    reset = sub.add_parser('reset-admin')
    reset.add_argument('username')
    upgrade = sub.add_parser('upgrade')
    upgrade.add_argument('source', type=Path)
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit('Use sudo narsika-admin.')
    if args.command == 'backup':
        backup()
    elif args.command == 'logs':
        run('journalctl', '-u', 'narsika', '-n', '100', '--no-pager')
    elif args.command == 'reset-admin':
        from install_native import service_environment
        with stopped():
            run('runuser', '-u', 'narsika', '--', BASE / 'current/.venv/bin/python',
                BASE / 'current/tools/bootstrap.py', '--reset-admin', args.username,
                env=service_environment(BASE / 'current'))
    elif args.command == 'upgrade':
        source = args.source.resolve()
        if not (source / 'tools/install_native.py').is_file():
            raise SystemExit('Pass the extracted new Narsika project directory.')
        run('bash', source / 'run_linux.sh')
    else:
        run('systemctl', args.command, 'narsika')


if __name__ == '__main__':
    main()
