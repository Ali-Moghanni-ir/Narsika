#!/usr/bin/env python3
"""Ubuntu native deployment. Repeated runs preserve data and keep previous releases."""
import fcntl
import grp
import ipaddress
import os
from pathlib import Path
import pwd
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.request

if sys.version_info < (3, 12):
    raise SystemExit('Native installation requires Python 3.12 or newer.')

ROOT = Path(__file__).resolve().parents[1]
BASE = Path('/opt/narsika')
CONFIG = Path('/etc/narsika/narsika.env')
DATA = Path('/var/lib/narsika')
UNIT = Path('/etc/systemd/system/narsika.service')
ADMIN_COMMAND = Path('/usr/local/bin/narsika-admin')
sys.path.insert(0, str(ROOT / 'tools'))
from package_release import release_files


def run(*args, **kwargs):
    return subprocess.run([str(a) for a in args], check=True, **kwargs)


def read_config(path=None):
    path=path or CONFIG
    return dict(line.split('=', 1) for line in path.read_text().splitlines()
                if line and not line.startswith('#') and '=' in line) if path.exists() else {}


def management_networks(value):
    result = []
    for item in value.split(','):
        network = ipaddress.IPv4Network(item.strip(), strict=True)
        if network.prefixlen == 0 or network.is_multicast or network.is_unspecified:
            raise ValueError('Use specific management networks, not 0.0.0.0/0.')
        if str(network) not in result:
            result.append(str(network))
    return result


def firewall_commands(networks, port):
    """Insert a port-specific deny first, then trusted sources ahead of it."""
    commands = [['ufw', 'insert', '1', 'deny', 'proto', 'tcp', 'from', 'any',
                 'to', 'any', 'port', str(port), 'comment', 'narsika-web-boundary']]
    for network in networks:
        commands.append(['ufw', 'insert', '1', 'allow', 'proto', 'tcp', 'from',
                         network, 'to', 'any', 'port', str(port),
                         'comment', 'narsika-management'])
    return commands


def write_private(path, content, uid=0, gid=0, mode=0o640):
    temporary = path.with_name(path.name + '.new-' + secrets.token_hex(6))
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(fd, 'w') as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    os.chown(temporary, uid, gid)
    os.replace(temporary, path)


def validate_service_account(account):
    """Keep service-file permissions independent from interactive Ubuntu accounts."""
    if account.pw_uid == 0 or account.pw_shell not in ('/usr/sbin/nologin', '/sbin/nologin'):
        raise SystemExit('Existing narsika OS account is not a non-login service account.')
    try:
        primary = grp.getgrgid(account.pw_gid)
    except KeyError:
        raise SystemExit('The narsika service account requires its own narsika group.') from None
    shared = any(user.pw_name != 'narsika' and user.pw_gid == account.pw_gid
                 for user in pwd.getpwall())
    if primary.gr_name != 'narsika' or primary.gr_gid == 0 or shared or any(
            name != 'narsika' for name in primary.gr_mem):
        raise SystemExit('The narsika service group must be dedicated; existing Ubuntu accounts were not changed.')
    groups = [group.gr_name for group in grp.getgrall()
              if 'narsika' in group.gr_mem or group.gr_gid == account.pw_gid]
    if any(group in groups for group in ('sudo', 'adm', 'docker', 'root')):
        raise SystemExit('Remove privileged memberships from the narsika service account before continuing.')


def health(port):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(40):
        try:
            with opener.open(f'http://127.0.0.1:{port}/healthz', timeout=2) as response:
                if response.status == 200:
                    return
        except OSError:
            pass
        time.sleep(1)
    raise RuntimeError('Service health check failed. Inspect: journalctl -u narsika -n 80')


def service_environment(release):
    return {**os.environ, 'NARSIKA_ENV_FILE': str(CONFIG), 'NARSIKA_DATA_DIR': str(DATA),
            'NARSIKA_BACKUP_DIR': str(DATA / 'backups'),
            'ANSIBLE_COLLECTIONS_PATH': str(release / '.venv/collections'),
            'PATH': str(release / '.venv/bin') + ':/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
            'PYTHONDONTWRITEBYTECODE': '1'}


def deploy():
    if os.geteuid() != 0 or not sys.stdin.isatty() or not sys.stdout.isatty():
        raise SystemExit('Use bash run_linux.sh from an interactive terminal.')
    os.umask(0o022)
    BASE.mkdir(parents=True, exist_ok=True, mode=0o755)
    with (BASE / 'install.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        install()


def install():
    if DATA.is_symlink() or CONFIG.is_symlink() or CONFIG.parent.is_symlink():
        raise SystemExit('Native data/configuration paths must be real paths, not symbolic links.')
    if (ROOT / 'instance/narsika.db').exists() and not (DATA / 'narsika.db').exists():
        raise SystemExit('A local legacy database exists. Follow docs/MIGRATION.md before installing; it was not replaced.')
    values = read_config()
    if values and (values.get('NARSIKA_DATA_DIR', str(DATA)) != str(DATA) or
                   values.get('NARSIKA_BACKUP_DIR', str(DATA / 'backups')) != str(DATA / 'backups')):
        raise SystemExit('Custom data paths require an explicit migration; existing paths were preserved.')
    try:
        port = int(input('HTTP port [8000]: ').strip() or values.get('NARSIKA_PORT', '8000'))
        if not 1024 <= port <= 65535:
            raise ValueError('Use a port from 1024 to 65535.')
        previous = values.get('NARSIKA_WEB_NETWORKS', '')
        prompt = 'Management IPv4 CIDR(s), comma separated'
        networks = management_networks(input(prompt + (f' [{previous}]' if previous else '') + ': ').strip() or previous)
    except ValueError as error:
        raise SystemExit(str(error)) from None
    if not networks:
        raise SystemExit('A management network is required.')
    active = subprocess.run(['systemctl', 'is-active', '--quiet', 'narsika']).returncode == 0
    enabled = subprocess.run(['systemctl', 'is-enabled', '--quiet', 'narsika']).returncode == 0
    if not active or port != int(values.get('NARSIKA_PORT', '8000')):
        with socket.socket() as probe:
            try:
                probe.bind(('0.0.0.0', port))
            except OSError:
                raise SystemExit('The selected port is already occupied.') from None
    if shutil.disk_usage(BASE).free < 2 * 1024**3:
        raise SystemExit('At least 2 GiB free space is required for a new release.')
    status = run('ufw', 'status', capture_output=True, text=True, env={**os.environ, 'LC_ALL': 'C'}).stdout
    enable_firewall = 'Status: inactive' in status
    if enable_firewall:
        print('UFW is inactive. Enabling it applies existing UFW policies to ALL services.')
        print('Narsika will preserve detected SSH access and add only its own scoped web rules.')
        if input('Review other listening services first. Enable UFW? Type ENABLE: ') != 'ENABLE':
            raise SystemExit('Firewall activation not confirmed; installation stopped.')
    if active:
        print('An upgrade will briefly stop Narsika. A full offline backup is optional.')
        if input('Create a private backup before upgrading? [y/N]: ').strip().lower() == 'y':
            run(sys.executable, BASE / 'current/tools/admin_native.py', 'backup')
    try:
        account = pwd.getpwnam('narsika')
    except KeyError:
        run('useradd', '--system', '--user-group', '--home-dir', str(DATA),
            '--no-create-home', '--shell', '/usr/sbin/nologin', 'narsika')
        account = pwd.getpwnam('narsika')
    validate_service_account(account)
    CONFIG.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    CONFIG.parent.chmod(0o750)
    os.chown(CONFIG.parent, 0, account.pw_gid)
    DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
    DATA.chmod(0o700)
    os.chown(DATA, account.pw_uid, account.pw_gid)
    release = BASE / 'releases' / (time.strftime('%Y%m%d-%H%M%S') + '-' + secrets.token_hex(4))
    release.mkdir(parents=True, mode=0o755)
    for source in release_files(ROOT):
        target = release / source.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        target.chmod(0o755 if target.suffix=='.sh' else 0o644)
    (release / 'playbooks').symlink_to('Playbooks/Original')
    run(sys.executable, '-m', 'venv', release / '.venv')
    python = release / '.venv/bin/python'
    run(python, '-m', 'pip', 'install', '--disable-pip-version-check',
        '--timeout', '120', '--retries', '10', '-r', release / 'requirements.txt', cwd=release)
    run(python, '-m', 'pip', 'check')
    run(release / '.venv/bin/ansible-galaxy', 'collection', 'install', '-r',
        release / 'Playbooks/requirements.yml', '-p', release / '.venv/collections')
    run(python, release / 'tools/patch_ansible.py', release / '.venv/collections')
    previous = (BASE / 'current').resolve() if (BASE / 'current').is_symlink() else None
    if (BASE / 'current').exists() and not previous:
        raise SystemExit('/opt/narsika/current is not an installer-managed symlink.')
    old_config = CONFIG.read_text() if CONFIG.exists() else None
    old_unit = UNIT.read_text() if UNIT.exists() else None
    old_admin = ADMIN_COMMAND.read_text() if ADMIN_COMMAND.exists() else None
    switched = False
    try:
        if active:
            run('systemctl', 'stop', 'narsika')
        # Offline imports may have been copied by root. Fix only this data tree.
        for path in DATA.rglob('*'):
            if path.is_symlink():continue
            os.chown(path,account.pw_uid,account.pw_gid)
            path.chmod(0o700 if path.is_dir() else 0o600)
        if not CONFIG.exists():
            run(python, release / 'configure.py', '--destination', CONFIG,
                '--web-networks', ','.join(networks), '--port', port)
        values = read_config()
        values.pop('NARSIKA_ADMIN_PASSWORD', None)
        values.pop('NARSIKA_ADMIN_PASSWORD_BASE64', None)
        values.pop('NARSIKA_HTTP_BIND', None)
        values.update(NARSIKA_BIND_ADDRESS='0.0.0.0', NARSIKA_PORT=str(port),
                      NARSIKA_WEB_NETWORKS=','.join(['127.0.0.0/8'] + networks),
                      NARSIKA_DATA_DIR=str(DATA), NARSIKA_BACKUP_DIR=str(DATA / 'backups'))
        write_private(CONFIG, ''.join(k + '=' + v + '\n' for k, v in values.items()), gid=account.pw_gid)
        run(python, release / 'configure.py', '--destination', CONFIG, '--check')
        run('runuser', '-u', 'narsika', '--', python, release / 'tools/bootstrap.py',
            env=service_environment(release))
        link = BASE / ('current.new-' + secrets.token_hex(4))
        link.symlink_to(release)
        os.replace(link, BASE / 'current')
        switched = True
        write_private(UNIT, (release / 'deploy/narsika.service').read_text(), mode=0o644)
        write_private(ADMIN_COMMAND,
                      '#!/bin/sh\nexec /usr/bin/python3 /opt/narsika/current/tools/admin_native.py "$@"\n',
                      mode=0o755)
        run('systemctl', 'daemon-reload')
        run('systemctl', 'enable', '--now', 'narsika')
        health(port)
        if enable_firewall:
            ssh = os.getenv('SSH_CONNECTION', '').split()
            if len(ssh) == 4:
                source = str(ipaddress.ip_address(ssh[0]))
                ssh_port = int(ssh[3])
                run('ufw', 'allow', 'proto', 'tcp', 'from', source, 'to', 'any', 'port', ssh_port,
                    'comment', 'narsika-preserve-current-ssh')
            # Preserve configured local SSH listeners, including non-standard ports.
            listeners = run('ss', '-H', '-lntp', capture_output=True, text=True).stdout
            for line in listeners.splitlines():
                if 'sshd' in line:
                    ssh_port = int(line.split()[3].rsplit(':', 1)[1])
                    run('ufw', 'allow', str(ssh_port) + '/tcp', 'comment', 'narsika-preserve-ssh')
        for command in firewall_commands(networks, port):
            run(*command)
        if enable_firewall:
            run('ufw', '--force', 'enable')
        run('ufw', 'status')
    except BaseException:
        if switched:
            subprocess.run(['systemctl', 'stop', 'narsika'])
            if not enabled:
                subprocess.run(['systemctl', 'disable', 'narsika'])
        if previous and switched:
            link = BASE / ('rollback-' + secrets.token_hex(4))
            link.symlink_to(previous)
            os.replace(link, BASE / 'current')
        if old_config is not None:
            write_private(CONFIG, old_config, gid=account.pw_gid)
        if old_unit is not None:
            write_private(UNIT, old_unit, mode=0o644)
        if old_admin is not None:
            write_private(ADMIN_COMMAND, old_admin, mode=0o755)
        subprocess.run(['systemctl', 'daemon-reload'])
        if active:
            subprocess.run(['systemctl', 'start', 'narsika'])
        print('Deployment failed. Previous release/config restored where present. Data, staged releases and firewall rules are preserved.', file=sys.stderr)
        raise
    addresses = run('hostname', '-I', capture_output=True, text=True).stdout.split()
    print('\nNarsika is healthy. HTTP is unencrypted; use only a trusted management LAN.')
    for address in addresses:
        if ':' not in address:
            print(f'  http://{address}:{port}')
    print('Listening on all IPv4 interfaces; only configured management sources may access it.')
    print('The host IP was not changed. Commands: sudo narsika-admin --help')


if __name__ == '__main__':
    try:
        deploy()
    except (subprocess.CalledProcessError, OSError, RuntimeError) as error:
        raise SystemExit('Installation stopped: ' + str(error)) from None
