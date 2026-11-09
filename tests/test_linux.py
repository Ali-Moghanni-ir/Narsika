"""Linux server support.

Parser tests use captured command output. The integration tests run an
in-process SSH server on 127.0.0.1 that executes each exec request with the
real /bin/sh of the test machine, so the collector scripts, the paramiko
session and Ansible's OpenSSH connection are exercised for real.
"""
import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import paramiko
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa

from app.models import Backup, Device, OperationRun, Playbook, RunArtifact, db
from app.security import APIError
from app.services import platforms
from app.services.network import key_host, known_path, probe_key, trust_key
from app.services.platforms import linux
from conftest import post

USER = 'linux-test'
PASSWORD = 'linux-test-password'


def marker(name: str, argument: str = '') -> str:
    return f'<<<narsika:{name}{" " + argument if argument else ""}>>>'


HEALTH_OUTPUT = '\n'.join([
    'Welcome! (printed by ~/.bashrc before the script starts)',
    marker('kernel'), 'Linux', '6.8.0-45-generic', 'x86_64',
    marker('hostname'), 'web-01',
    marker('os-release'), 'NAME="Ubuntu"', 'VERSION_ID="24.04"', 'PRETTY_NAME="Ubuntu 24.04.1 LTS"', 'ID=ubuntu',
    marker('cpu'),
    'cpu  1000 0 500 8000 500 0 0 0 0 0',
    'cpu  1300 0 600 8500 600 0 0 0 0 0',
    marker('memory'), 'MemTotal:        8000000 kB', 'MemFree:          500000 kB',
    'MemAvailable:    6000000 kB', 'HugePages_Total:       0',
    marker('uptime'), '93784.52 350000.00',
    marker('load'), '0.52 0.58 0.59 1/467 12345',
    marker('cpus'), '4',
    marker('filesystems'),
    'Filesystem     1024-blocks      Used Available Capacity Mounted on',
    'overlay           41152736  12345678  26677890      32% /',
    'tmpfs              8000000         0   8000000       0% /dev/shm',
    '/dev/sda2          1000000    950000     50000      95% /var/log',
    '/dev/loop3           65536     65536         0     100% /snap/core22/1564',
    '/dev/sdb1        976762584 100000000 876762584      11% /srv/data with space',
    marker('temperature'), '-273000', '0', '45000', '61500', '999999',
    marker('model'), 'To Be Filled By O.E.M.', 'PowerEdge R640',
    marker('board'),
    marker('failed-units'),
    marker('end'),
]) + '\n'


def interface_output(first: list[str], second: list[str], start: str = '100.00', end: str = '101.00') -> str:
    return '\n'.join([marker('kernel'), 'Linux', marker('sample', start), *first, marker('sample', end), *second,
                      marker('end')]) + '\n'


def row(name: str, index: int, state: str, carrier: str, speed: str, rx: int, tx: int) -> str:
    return '\t'.join([name, str(index), state, carrier, speed, str(rx), str(tx), '0', '2'])


# Parsers ---------------------------------------------------------------------

def test_linux_platform_offers_monitoring_backup_and_playbooks_only():
    platform = platforms.get('linux')
    assert platform.vendor == 'Linux' and platform.os_type == 'linux'
    assert platform.capabilities == {platforms.HEALTH, platforms.INTERFACES, platforms.BACKUP, platforms.PLAYBOOKS}
    assert platforms.by_vendor('Linux') is platform
    assert 'Linux' in platforms.vendors()


def test_health_parser_reads_real_formats_and_leaves_unknown_values_empty():
    health = linux.parse_health(HEALTH_OUTPUT)
    # (300 user + 100 system) busy out of 1000 total jiffies; iowait counts as idle.
    assert health['cpu_percent'] == 40.0
    assert health['memory_total_bytes'] == 8000000 * 1024
    assert health['memory_used_bytes'] == 2000000 * 1024
    assert health['memory_free_bytes'] == 6000000 * 1024
    assert health['memory_percent'] == 25.0
    assert health['uptime'] == '1 day, 2 hours, 3 minutes' and health['uptime_seconds'] == 93784
    assert health['load_average'] == [0.52, 0.58, 0.59] and health['cpu_count'] == 4
    assert health['os_version'] == 'Ubuntu 24.04.1 LTS'
    assert (health['kernel'], health['architecture'], health['hostname']) == ('6.8.0-45-generic', 'x86_64', 'web-01')
    # A placeholder vendor is dropped instead of being shown as a model.
    assert health['model'] == 'PowerEdge R640'
    # Only plausible sensor readings count.
    assert health['temperature_celsius'] == 61.5
    mounts = [disk['mount'] for disk in health['filesystems']]
    assert mounts == ['/', '/var/log', '/srv/data with space']
    assert (health['disk_percent'], health['disk_mount']) == (95, '/var/log')
    # No systemd answer means unknown, not zero failed units.
    assert health['failed_units'] is None and health['failed_unit_names'] is None


def test_health_parser_counts_failed_units_only_when_systemd_answered():
    output = HEALTH_OUTPUT.replace(marker('failed-units') + '\n', marker('failed-units') + '\n'
                                   'nginx.service loaded failed failed A web server\n'
                                   'backup.timer loaded failed failed Nightly backup\n' + marker('systemd') + '\n')
    health = linux.parse_health(output)
    assert health['failed_units'] == 2 and health['failed_unit_names'] == ['nginx.service', 'backup.timer']
    healthy = HEALTH_OUTPUT.replace(marker('failed-units') + '\n', marker('failed-units') + '\n' + marker('systemd') + '\n')
    assert linux.parse_health(healthy)['failed_units'] == 0


def test_health_parser_keeps_missing_measurements_as_none():
    output = '\n'.join([marker('kernel'), 'Linux', marker('cpu'), 'cpu garbage', marker('memory'), 'MemTotal: 100 kB',
                        marker('uptime'), 'not-a-number', marker('load'), '0.1 0.2', marker('model'), '', '',
                        marker('board'), 'Raspberry Pi 4 Model B Rev 1.4', marker('end')])
    health = linux.parse_health(output)
    for key in ('cpu_percent', 'memory_percent', 'memory_used_bytes', 'memory_free_bytes', 'uptime', 'uptime_seconds',
                'temperature_celsius', 'os_version', 'load_average', 'cpu_count', 'disk_percent', 'failed_units'):
        assert health[key] is None, key
    assert health['memory_total_bytes'] == 102400
    assert health['model'] == 'Raspberry Pi 4 Model B Rev 1.4'
    assert health['filesystems'] == []


@pytest.mark.parametrize('output,code', [
    (HEALTH_OUTPUT.replace(marker('end'), ''), 'DEVICE_ERROR'),
    (HEALTH_OUTPUT.replace('\nLinux\n', '\nFreeBSD\n', 1), 'UNSUPPORTED'),
    ('This account is currently not available.\n', 'DEVICE_ERROR'),
])
def test_health_parser_rejects_incomplete_or_foreign_output(output, code):
    with pytest.raises(APIError) as caught:
        linux.parse_health(output)
    assert caught.value.code == code


def test_cpu_parser_handles_old_kernels_and_counter_problems():
    assert linux.cpu_percent('cpu 100 0 100 800', 'cpu 150 0 150 900') == 50.0
    assert linux.cpu_percent('cpu 100 0 100 800 0 0 0 0', 'cpu 100 0 100 800 0 0 0 0') is None
    assert linux.cpu_percent('cpu 100 0 100 800', 'cpu 90 0 100 800') is None
    assert linux.cpu_percent('cpu ２ 0 0 0', 'cpu 3 0 0 0') is None


def test_interface_parser_computes_rates_and_skips_counter_resets():
    first = [row('eth0', 2, 'up', '1', '1000', 1000, 5000), row('wg0', 7, 'unknown', '1', '', 10, 10),
             row('eth1', 3, 'down', '', '-1', 900, 900), row('br0', 4, 'dormant', '', '', 0, 0)]
    second = [row('eth0', 2, 'up', '1', '1000', 126000, 5000), row('wg0', 7, 'unknown', '1', '', 20, 30),
              row('eth1', 3, 'down', '', '-1', 100, 900), row('br0', 4, 'dormant', '', '', 0, 0),
              row('veth9', 9, 'up', '1', '10000', 50, 50)]
    rows = linux.parse_interfaces(interface_output(first, second, '100.00', '100.50'))
    assert [item['name'] for item in rows] == ['eth0', 'eth1', 'br0', 'wg0', 'veth9']
    eth0, eth1, br0, wg0, veth9 = rows
    assert eth0['index'] == 2 and eth0['status'] == 'online' and eth0['speed_mbps'] == 1000.0
    assert eth0['rx_bps'] == 2000000.0 and eth0['tx_bps'] == 0.0
    assert eth0['tx_errors'] == 2
    assert (eth1['status'], eth1['speed_mbps'], eth1['rx_bps']) == ('offline', None, None)
    assert br0['status'] == 'unknown'
    assert wg0['status'] == 'online' and wg0['rx_bps'] == 160.0
    # An interface that appeared between the samples has no rate yet.
    assert veth9['rx_bps'] is None


def test_interface_parser_requires_two_samples_from_linux():
    with pytest.raises(APIError) as caught:
        linux.parse_interfaces('\n'.join([marker('kernel'), 'Linux', marker('sample', '1.0'), marker('end')]))
    assert caught.value.code == 'DEVICE_ERROR'
    with pytest.raises(APIError) as caught:
        linux.parse_interfaces(interface_output([], []).replace('\nLinux\n', '\nDarwin\n'))
    assert caught.value.code == 'UNSUPPORTED'


def test_snapshot_drops_shell_noise_and_binary_content():
    raw = '\n'.join(['motd noise', '##### narsika: system', 'Linux 6.8.0 x86_64', '##### narsika: file /etc/hosts',
                     '127.0.0.1 localhost', '##### narsika: file /etc/odd', 'text\x00binary', 'more',
                     '##### narsika: end of snapshot', 'trailing noise'])
    assert linux.clean_snapshot(raw) == ('##### narsika: system\nLinux 6.8.0 x86_64\n##### narsika: file /etc/hosts\n'
                                         '127.0.0.1 localhost\n##### narsika: file /etc/odd\n'
                                         '(binary content not included)\n##### narsika: end of snapshot\n')
    with pytest.raises(APIError) as caught:
        linux.clean_snapshot('##### narsika: system\nLinux\n')
    assert caught.value.code == 'PARTIAL_EXPORT'


# Ansible connection ------------------------------------------------------------

def openssh_key(key: object, passphrase: bytes | None = None) -> str:
    encryption = serialization.BestAvailableEncryption(passphrase) if passphrase else serialization.NoEncryption()
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH, encryption).decode()


def test_private_keys_are_converted_for_the_run_scoped_ssh_agent():
    rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = rsa_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                serialization.NoEncryption()).decode()
    # A stray passphrase on an unencrypted key is ignored, as paramiko does.
    converted = linux.openssh_private_key({'private_key': pem, 'passphrase': 'unused'})
    assert converted.startswith('-----BEGIN OPENSSH PRIVATE KEY-----')
    loaded = serialization.load_ssh_private_key(converted.encode(), password=None)
    assert loaded.public_key().public_numbers() == rsa_key.public_key().public_numbers()
    edwards = ed25519.Ed25519PrivateKey.generate()
    protected = openssh_key(edwards, b'correct horse')
    assert linux.openssh_private_key({'private_key': protected, 'passphrase': 'correct horse'}).startswith('-----BEGIN OPENSSH')
    for values in ({'private_key': protected, 'passphrase': 'wrong'}, {'private_key': protected},
                   {'private_key': 'not a key'}):
        with pytest.raises(APIError) as caught:
            linux.openssh_private_key(values)
        assert caught.value.code == 'AUTH_FAILED'


def test_ansible_connection_keeps_host_key_checking_strict(app):
    target = SimpleNamespace(ip_address='10.0.0.5', ssh_port=2222)
    with app.app_context():
        variables = linux.ansible_host_vars(target, 'ops', {'password': 'login-secret'}, Path(app.config['DATA_DIR']))
        options = variables['ansible_ssh_common_args']
        assert variables['ansible_connection'] == 'ssh' and variables['ansible_host_key_checking'] is True
        assert '-F /dev/null' in options and 'StrictHostKeyChecking=yes' in options
        assert f'UserKnownHostsFile="{known_path()}"' in options and 'GlobalKnownHostsFile=/dev/null' in options
        assert 'UpdateHostKeys=no' in options and 'PubkeyAuthentication=no' in options
        assert 'login-secret' not in options
        # sudo falls back to the login password; a separate one wins when stored.
        assert variables['ansible_become_password'] == 'login-secret'
        assert 'ansible_become' not in variables and 'ansible_become_method' not in variables
        separate = linux.ansible_host_vars(target, 'ops', {'password': 'a', 'enable_password': 'b'}, Path('/tmp'))
        assert separate['ansible_become_password'] == 'b'
        key = openssh_key(ed25519.Ed25519PrivateKey.generate())
        keyed = linux.ansible_host_vars(target, 'ops', {'private_key': key}, Path('/tmp'))
        assert keyed['ansible_ssh_private_key'].startswith('-----BEGIN OPENSSH PRIVATE KEY-----')
        assert 'PubkeyAuthentication=no' not in keyed['ansible_ssh_common_args']
        assert 'ansible_password' not in keyed and 'ansible_become_password' not in keyed
    environment = linux.ansible_environment({'private_key': key}, Path('/tmp/nsk-test'))
    assert environment['ANSIBLE_SSH_AGENT'] == 'auto' and environment['ANSIBLE_PIPELINING'] == 'True'
    assert 'ControlPersist' not in environment['ANSIBLE_SSH_ARGS']
    assert environment['ANSIBLE_LOCAL_TEMP'] == '/tmp/nsk-test/local'
    assert 'ANSIBLE_SSH_AGENT' not in linux.ansible_environment({'password': 'x'}, Path('/tmp/nsk-test'))


@pytest.mark.parametrize('path', ['/var/lib/%h', '/var/lib/${HOME}', '/var/lib/"quoted"', 'relative/path', '/a\\b'])
def test_known_hosts_path_with_expandable_characters_is_refused(path):
    with pytest.raises(APIError) as caught:
        linux.ssh_option_path(Path(path))
    assert caught.value.code == 'CONFIGURATION_ERROR'


def test_vlan_and_firewall_refuse_linux_devices_before_any_connection(admin, app):
    profile = post(admin, '/api/credentials', {'name': 'Linux profile', 'username': USER, 'kind': 'ssh',
                                               'password': PASSWORD}).json['data']
    response = post(admin, '/api/devices', {'name': 'web-01', 'ip_address': '10.0.0.9', 'platform': 'linux',
                                            'credential_id': profile['id']})
    assert response.status_code == 201, response.json
    ident = response.json['data']['id']
    vlans = admin.get(f'/api/devices/{ident}/vlans')
    assert vlans.status_code == 422 and vlans.json['error']['code'] == 'UNSUPPORTED'
    run = post(admin, '/api/automation/runs', {'kind': 'vlan', 'device_id': ident,
                                               'parameters': {'vlan_id': 20, 'vlan_name': 'users'}})
    assert run.status_code == 422 and run.json['error']['code'] == 'UNSUPPORTED'
    firewall = post(admin, f'/api/firewall/devices/{ident}/refresh', {})
    assert firewall.status_code == 422 and firewall.json['error']['code'] == 'UNSUPPORTED'
    with app.app_context():
        cisco_book = Playbook.query.filter_by(name='Cisco/01_health_report.yml').one().id
    wrong = post(admin, '/api/automation/runs', {'kind': 'playbook', 'device_id': ident, 'playbook_id': cisco_book})
    assert wrong.status_code == 422


# In-process SSH server ----------------------------------------------------------

def execute(channel: paramiko.Channel, command: bytes) -> None:
    """Run one exec request with the local /bin/sh, like sshd does with the user's shell."""
    try:
        process = subprocess.Popen(['/bin/sh', '-c', command.decode()], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
    except OSError:
        channel.send_exit_status(127)
        channel.close()
        return

    def feed() -> None:
        try:
            while True:
                data = channel.recv(65536)
                if not data:
                    break
                process.stdin.write(data)
                process.stdin.flush()
        except (OSError, EOFError, socket.timeout):
            pass
        finally:
            try:
                process.stdin.close()
            except OSError:
                pass

    def pump(stream, send) -> None:
        try:
            for chunk in iter(lambda: stream.read1(65536), b''):
                send(chunk)
        except (OSError, EOFError):
            pass

    threading.Thread(target=feed, daemon=True).start()
    readers = [threading.Thread(target=pump, args=(process.stdout, channel.sendall), daemon=True),
               threading.Thread(target=pump, args=(process.stderr, channel.sendall_stderr), daemon=True)]
    for reader in readers:
        reader.start()
    for reader in readers:
        reader.join()
    status = process.wait()
    try:
        channel.send_exit_status(status)
        channel.shutdown_write()
        channel.close()
    except (OSError, EOFError):
        pass


class LinuxServer(paramiko.ServerInterface):
    def __init__(self, client_key: str | None):
        # Base64 of the one public key accepted for USER, as in an authorized_keys line.
        self.client_key = client_key

    def check_auth_password(self, username, password):
        return paramiko.AUTH_SUCCESSFUL if (username, password) == (USER, PASSWORD) else paramiko.AUTH_FAILED

    def check_auth_publickey(self, username, key):
        allowed = self.client_key is not None and key.get_base64() == self.client_key
        return paramiko.AUTH_SUCCESSFUL if username == USER and allowed else paramiko.AUTH_FAILED

    def get_allowed_auths(self, username):
        return 'publickey,password'

    def check_channel_request(self, kind, chanid):
        return paramiko.OPEN_SUCCEEDED if kind == 'session' else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_pty_request(self, *args):
        # Ansible asks for a terminal for some commands; plain pipes are enough here.
        return True

    def check_channel_exec_request(self, channel, command):
        threading.Thread(target=execute, args=(channel, command), daemon=True).start()
        return True


@pytest.fixture
def linux_server():
    """Yields (port, host key, client key setter) for a loopback SSH server that runs real commands."""
    host_key = paramiko.ECDSAKey.generate()
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen(16)
    listener.settimeout(0.2)
    stop = threading.Event()
    state = {'client_key': None}
    transports = []

    def handle(connection: socket.socket) -> None:
        transport = paramiko.Transport(connection)
        transports.append(transport)
        # paramiko closes a channel when its object is garbage collected; keep every accepted one.
        channels = []
        try:
            transport.add_server_key(host_key)
            transport.start_server(server=LinuxServer(state['client_key']))
            while transport.is_active() and not stop.is_set():
                channel = transport.accept(0.2)
                if channel is not None:
                    channels.append(channel)
        except (EOFError, OSError, paramiko.SSHException):
            pass
        finally:
            transport.close()

    def loop() -> None:
        while not stop.is_set():
            try:
                connection, _ = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=handle, args=(connection,), daemon=True).start()

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
    yield SimpleNamespace(port=listener.getsockname()[1], host_key=host_key,
                          allow_key=lambda key: state.update(client_key=key))
    stop.set()
    listener.close()
    for transport in transports:
        transport.close()
    thread.join(timeout=2)


def linux_device(admin, port: int, secret: dict | None = None) -> int:
    values = {'password': PASSWORD} if secret is None else secret
    profile = post(admin, '/api/credentials', {'name': 'Linux SSH ' + str(time.time_ns()), 'username': USER,
                                               'kind': 'ssh', **values})
    assert profile.status_code == 201, profile.json
    response = post(admin, '/api/devices', {'name': 'Loopback Linux', 'ip_address': '127.0.0.1', 'platform': 'linux',
                                            'ssh_port': port, 'credential_id': profile.json['data']['id']})
    assert response.status_code == 201, response.json
    return response.json['data']['id']


def trust(app, port: int) -> None:
    with app.app_context():
        record, _ = probe_key('127.0.0.1', port)
        trust_key('127.0.0.1', port, record['fingerprint'])
    fresh_samples(app)


def fresh_samples(app) -> None:
    """Telemetry reuses a sample for four seconds; tests that change trust or keys need a new one."""
    app.extensions.get('telemetry_samples', {}).clear()


def test_linux_health_interfaces_and_identity_over_a_real_ssh_session(admin, app, linux_server):
    ident = linux_device(admin, linux_server.port)
    untrusted = admin.get(f'/api/devices/{ident}/health').json['data']
    assert untrusted['connection_status'] == 'HOST_KEY_REQUIRED'
    trust(app, linux_server.port)
    health = admin.get(f'/api/devices/{ident}/health').json['data']
    assert health['connection_status'] == 'CONNECTED', health['error']
    assert health['status'] in ('online', 'degraded')
    assert health['kernel'] == os.uname().release and health['hostname'] == os.uname().nodename
    assert 0 <= health['cpu_percent'] <= 100 and 0 < health['memory_percent'] < 100
    assert health['uptime_seconds'] > 0 and health['cpu_count'] >= 1
    assert any(disk['mount'] == '/' for disk in health['filesystems'])
    interfaces = admin.get(f'/api/devices/{ident}/interfaces').json['data']
    assert interfaces['transport'] == 'SSH' and interfaces['rate_note'] == linux.INTERFACE_RATE_NOTE
    names = {item['name'] for item in interfaces['items']}
    assert names == {name for name in os.listdir('/sys/class/net') if name != 'lo' and os.path.isdir('/sys/class/net/' + name)}
    assert all(item['status'] in ('online', 'offline', 'unknown') for item in interfaces['items'])
    with app.app_context():
        credential = db.session.get(Device, ident).credential
        target = SimpleNamespace(id='candidate-1', ip_address='127.0.0.1', ssh_port=linux_server.port,
                                 platform='linux', credential=credential)
        assert platforms.driver(target).verify_identity(target) is True


def test_linux_backup_runs_through_the_job_queue(admin, app, linux_server):
    from app.services.jobs import JobManager
    ident = linux_device(admin, linux_server.port)
    trust(app, linux_server.port)
    response = post(admin, '/api/backups', {'device_id': ident})
    assert response.status_code == 202, response.json
    run_id = response.json['data']['run_id']
    app.extensions['jobs'] = JobManager(app)
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        with app.app_context():
            run = db.session.get(OperationRun, run_id)
            if run.status not in ('PENDING', 'RUNNING'):
                break
        time.sleep(0.2)
    with app.app_context():
        run = db.session.get(OperationRun, run_id)
        assert run.status == 'SUCCESS', run.output
        backup = Backup.query.filter_by(device_id=ident).one()
    content = admin.get(f'/api/backups/{backup.id}').json['data']['content']
    assert content.startswith('##### narsika: Linux configuration snapshot')
    assert content.rstrip().endswith('##### narsika: end of snapshot')
    assert '##### narsika: file /etc/passwd' in content
    assert PASSWORD not in content


def test_linux_session_rejects_changed_keys_and_wrong_passwords(admin, app, linux_server):
    ident = linux_device(admin, linux_server.port, {'password': 'not-the-password'})
    trust(app, linux_server.port)
    assert admin.get(f'/api/devices/{ident}/health').json['data']['connection_status'] == 'AUTH_FAILED'
    with app.app_context():
        keys = paramiko.HostKeys(str(known_path()))
        other = paramiko.ECDSAKey.generate()
        keys.add(key_host('127.0.0.1', linux_server.port), other.get_name(), other)
        keys.save(str(known_path()))
        before = known_path().read_bytes()
    fresh_samples(app)
    assert admin.get(f'/api/devices/{ident}/health').json['data']['connection_status'] == 'HOST_KEY_CHANGED'
    with app.app_context():
        assert known_path().read_bytes() == before


def test_linux_refused_connection_is_reported_as_unreachable(admin, app):
    closed = socket.socket()
    closed.bind(('127.0.0.1', 0))
    port = closed.getsockname()[1]
    closed.close()
    ident = linux_device(admin, port)
    with app.app_context():
        keys = paramiko.HostKeys(str(known_path()))
        trusted = paramiko.ECDSAKey.generate()
        keys.add(key_host('127.0.0.1', port), trusted.get_name(), trusted)
        keys.save(str(known_path()))
    health = admin.get(f'/api/devices/{ident}/health').json['data']
    assert health['connection_status'] == 'UNREACHABLE'
    assert health['cpu_percent'] is None and health['os_version'] is None


def ansible_ready() -> bool:
    if not (shutil.which('ssh') and shutil.which('ssh-agent')):
        return False
    try:
        probe = socket.socket(socket.AF_UNIX)
        probe.close()
    except (OSError, PermissionError):
        return False
    return True


def run_linux_playbook(app, ident: int, name: str):
    from app.services.automation import run_ansible
    from app.services.jobs import enqueue
    with app.app_context():
        book = Playbook.query.filter_by(name=name).one()
        params = {'playbook_id': book.id, 'variables': {}}
        run = enqueue('playbook', ident, 1, params)
        db.session.commit()
        try:
            return run_ansible(run, params, db.session.get(Device, ident)), run.output, None
        except APIError as error:
            return None, run.output, error


@pytest.mark.skipif(not ansible_ready(), reason='Needs the OpenSSH client, ssh-agent and Unix sockets.')
def test_ansible_runs_the_linux_health_playbook_over_openssh(admin, app, linux_server):
    ident = linux_device(admin, linux_server.port)
    trust(app, linux_server.port)
    result, output, error = run_linux_playbook(app, ident, 'Linux/01_health_report.yml')
    assert error is None, output
    assert 'completed' in result and 'OK [ansible.builtin.setup]' in output
    assert PASSWORD not in output
    with app.app_context():
        artifact = RunArtifact.query.one()
        assert artifact.name == 'health-report.json'


@pytest.mark.skipif(not ansible_ready(), reason='Needs the OpenSSH client, ssh-agent and Unix sockets.')
def test_ansible_uses_a_passphrase_protected_key_through_its_agent(admin, app, linux_server):
    key = ed25519.Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH)
    linux_server.allow_key(public.decode().split()[1])
    ident = linux_device(admin, linux_server.port, {'private_key': openssh_key(key, b'agent passphrase'),
                                                     'passphrase': 'agent passphrase'})
    trust(app, linux_server.port)
    result, output, error = run_linux_playbook(app, ident, 'Linux/01_health_report.yml')
    assert error is None, output
    assert 'completed' in result


@pytest.mark.skipif(not ansible_ready(), reason='Needs the OpenSSH client, ssh-agent and Unix sockets.')
def test_ansible_never_accepts_an_untrusted_linux_host_key(admin, app, linux_server):
    ident = linux_device(admin, linux_server.port)
    with app.app_context():
        before = known_path().read_bytes()
    result, output, error = run_linux_playbook(app, ident, 'Linux/01_health_report.yml')
    assert result is None and error.code == 'ANSIBLE_FAILED'
    # Ansible reports a refused password login to an unknown key as a failed task, not as unreachable.
    assert 'FAILED [ansible.builtin.setup]' in output and 'OK [ansible.builtin.setup]' not in output
    with app.app_context():
        assert known_path().read_bytes() == before
