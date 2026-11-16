"""SSH host key diagnostics, legacy ssh-rsa compatibility and the isolated Docker network.

Network checks run against loopback sockets and, where OpenSSH's sshd is installed, a real
loopback server that only signs with legacy ssh-rsa. No external network access.
"""
import errno
import ipaddress
import shlex
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import paramiko
import pytest
import yaml

from app import create_app
from app.models import db
from app.security import APIError
from app.services import network
from app.services.network import HandshakeTimeout, key_host, known_path, legacy_rsa_only, probe_failure, probe_key
from app.services.platforms import linux, network_cli

ROOT = Path(__file__).resolve().parents[1]


def banner_error(cause: BaseException) -> paramiko.SSHException:
    """The exception paramiko raises when the banner read fails, with its implicit context."""
    try:
        try:
            raise cause
        except BaseException as inner:
            raise paramiko.SSHException('Error reading SSH protocol banner' + str(inner))
    except paramiko.SSHException as error:
        return error


@pytest.mark.parametrize('error,code', [
    (ConnectionRefusedError(errno.ECONNREFUSED, 'Connection refused'), 'SSH_REFUSED'),
    (OSError(errno.ENETUNREACH, 'Network is unreachable'), 'NO_ROUTE'),
    (OSError(errno.EHOSTUNREACH, 'No route to host'), 'NO_ROUTE'),
    (socket.timeout('timed out'), 'SSH_TIMEOUT'),
    (TimeoutError('timed out'), 'SSH_TIMEOUT'),
    (PermissionError(errno.EPERM, 'Operation not permitted'), 'LOCAL_NETWORK_DENIED'),
    (OSError(errno.EACCES, 'Permission denied'), 'LOCAL_NETWORK_DENIED'),
    (paramiko.ssh_exception.IncompatiblePeer('Incompatible ssh peer (no acceptable host key)'),
     'SSH_ALGORITHM_UNSUPPORTED'),
    (banner_error(EOFError()), 'SSH_HANDSHAKE_FAILED'),
    (banner_error(socket.timeout()), 'NOT_SSH'),
    (paramiko.SSHException('Indecipherable protocol version "HTTP/1.1 400"'), 'NOT_SSH'),
    (EOFError(), 'SSH_HANDSHAKE_FAILED'),
    (HandshakeTimeout(banner_seen=False), 'NOT_SSH'),
    (HandshakeTimeout(banner_seen=True), 'SSH_HANDSHAKE_FAILED'),
    (paramiko.SSHException('Negotiation failed.'), 'SSH_HANDSHAKE_FAILED'),
])
def test_host_key_failures_name_their_cause(error, code):
    message, found, status = probe_failure(error, 1284)
    assert found == code
    assert status == (503 if code == 'LOCAL_NETWORK_DENIED' else 502)
    assert message != 'Unable to retrieve the SSH host key.'


def test_network_failures_point_at_docker_subnet_overlap():
    for error in (socket.timeout('timed out'), OSError(errno.EHOSTUNREACH, 'No route to host')):
        assert 'NARSIKA_DOCKER_SUBNET' in probe_failure(error, 22)[0]
    assert 'port 1284' in probe_failure(ConnectionRefusedError(errno.ECONNREFUSED, 'refused'), 1284)[0]


def serve(handler):
    """Accept one loopback connection and pass it to handler; returns the port."""
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen(1)

    def run():
        with listener:
            conn, _ = listener.accept()
            with conn:
                handler(conn)

    threading.Thread(target=run, daemon=True).start()
    return listener.getsockname()[1]


def probe_code(app, port):
    with app.app_context():
        with pytest.raises(APIError) as caught:
            probe_key('127.0.0.1', port)
    return caught.value.code, caught.value.message


def test_real_sockets_are_classified(app, monkeypatch):
    monkeypatch.setattr(network, 'PROBE_TIMEOUT', 1)
    closed = socket.socket()
    closed.bind(('127.0.0.1', 0))
    port = closed.getsockname()[1]
    closed.close()
    assert probe_code(app, port)[0] == 'SSH_REFUSED'
    assert probe_code(app, serve(lambda conn: None))[0] == 'SSH_HANDSHAKE_FAILED'
    assert probe_code(app, serve(lambda conn: time.sleep(3)))[0] == 'NOT_SSH'
    web = serve(lambda conn: conn.sendall(b'HTTP/1.1 400 Bad Request\r\n\r\n'))
    assert probe_code(app, web)[0] in ('NOT_SSH', 'SSH_HANDSHAKE_FAILED')


def pin(app, ip, port, key):
    with app.app_context():
        keys = paramiko.HostKeys()
        keys.add(key_host(ip, port), key.get_name(), key)
        keys.save(str(known_path()))


def test_legacy_rsa_options_only_for_rsa_pinned_hosts(app, tmp_path):
    target = SimpleNamespace(ip_address='10.0.0.9', ssh_port=1284)
    values = {'password': 'fixture-password'}
    with app.app_context():
        assert not legacy_rsa_only('10.0.0.9', 1284)
        modern = network_cli.ansible_host_vars(target, 'admin', values, tmp_path, 'community.routeros.routeros', False)
        assert 'ansible_libssh_hostkeys' not in modern
        assert 'HostKeyAlgorithms' not in linux.ansible_host_vars(target, 'admin', values, tmp_path)['ansible_ssh_common_args']
    pin(app, '10.0.0.9', 1284, paramiko.RSAKey.generate(2048))
    with app.app_context():
        assert legacy_rsa_only('10.0.0.9', 1284)
        assert not legacy_rsa_only('10.0.0.9', 22)
        legacy = network_cli.ansible_host_vars(target, 'admin', values, tmp_path, 'community.routeros.routeros', False)
        assert legacy['ansible_libssh_hostkeys'] == 'rsa-sha2-512,rsa-sha2-256,ssh-rsa'
        # Host key checking stays strict; only the signature algorithms widen.
        assert legacy['ansible_libssh_host_key_checking'] is True and legacy['ansible_libssh_host_key_auto_add'] is False
        args = linux.ansible_host_vars(target, 'admin', values, tmp_path)['ansible_ssh_common_args']
        assert 'HostKeyAlgorithms=rsa-sha2-512,rsa-sha2-256,ssh-rsa' in args and 'StrictHostKeyChecking=yes' in args
    pin(app, '10.0.0.9', 1284, paramiko.ECDSAKey.generate())
    with app.app_context():
        assert not legacy_rsa_only('10.0.0.9', 1284)


@pytest.fixture
def legacy_sshd(tmp_path):
    """A loopback OpenSSH server whose only host key algorithm is SHA-1 ssh-rsa, as on old RouterOS."""
    sshd = shutil.which('sshd') or ('/usr/sbin/sshd' if Path('/usr/sbin/sshd').exists() else None)
    if not sshd or not shutil.which('ssh') or not shutil.which('ssh-keygen'):
        pytest.skip('OpenSSH server and client are not installed')
    subprocess.run(['ssh-keygen', '-q', '-t', 'rsa', '-b', '2048', '-N', '', '-f', str(tmp_path / 'hostkey')], check=True)
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    config = tmp_path / 'sshd_config'
    config.write_text(f'Port {port}\nListenAddress 127.0.0.1\nHostKey {tmp_path / "hostkey"}\n'
                      f'HostKeyAlgorithms ssh-rsa\nPidFile {tmp_path / "sshd.pid"}\nUsePAM no\n')
    server = subprocess.Popen([sshd, '-D', '-e', '-f', str(config)], stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                socket.create_connection(('127.0.0.1', port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.skip('sshd did not start in this environment')
        yield port
    finally:
        server.terminate()
        server.wait(timeout=5)


def test_openssh_reaches_a_legacy_ssh_rsa_server_with_the_pinned_key(app, tmp_path, legacy_sshd):
    port = legacy_sshd
    with app.app_context():
        record, key = probe_key('127.0.0.1', port)
    assert record['key_type'] == 'ssh-rsa'
    pin(app, '127.0.0.1', port, key)
    target = SimpleNamespace(ip_address='127.0.0.1', ssh_port=port)
    with app.app_context():
        args = linux.ansible_host_vars(target, 'nobody', {'password': 'x'}, tmp_path)['ansible_ssh_common_args']
    base = ['ssh', '-p', str(port), '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5']
    options = shlex.split(args)
    legacy = subprocess.run(base + options + ['nobody@127.0.0.1', 'true'], capture_output=True, text=True, timeout=20)
    # The handshake and the strict host key check pass; only authentication is refused.
    assert 'Permission denied' in legacy.stderr, legacy.stderr
    index = options.index('HostKeyAlgorithms=rsa-sha2-512,rsa-sha2-256,ssh-rsa')
    default = options[:index - 1] + options[index + 1:]
    refused = subprocess.run(base + default + ['nobody@127.0.0.1', 'true'], capture_output=True, text=True, timeout=20)
    # OpenSSH 8.8 and later refuse such a server without the option.
    assert 'no matching host key type' in refused.stderr, refused.stderr


def test_libssh_reaches_a_legacy_ssh_rsa_server_with_the_pinned_key(app, legacy_sshd):
    session_module = pytest.importorskip('pylibsshext.session')
    errors = pytest.importorskip('pylibsshext.errors')
    port = legacy_sshd
    with app.app_context():
        _, key = probe_key('127.0.0.1', port)
    pin(app, '127.0.0.1', port, key)
    with app.app_context():
        known = str(known_path())
    session = session_module.Session()
    with pytest.raises(errors.LibsshSessionException) as caught:
        session.connect(host='127.0.0.1', port=port, user='nobody', password='x', host_key_checking=True,
                        look_for_keys=False, knownhosts=known, timeout=5,
                        hostkeys=network_cli.RSA_HOST_KEY_ALGORITHMS)
    # Past key exchange and the strict host key check; the fixture account cannot sign in.
    assert 'kex error' not in str(caught.value) and 'authenticate' in str(caught.value).lower()


def compose() -> dict:
    return yaml.safe_load((ROOT / 'docker-compose.yml').read_text())


def default(value: str) -> str:
    """The default of a Compose ${NAME:-default} expression."""
    return value.split(':-', 1)[1].rstrip('}')


def test_docker_network_avoids_enterprise_and_docker_default_ranges():
    data = compose()
    ipam = data['networks']['narsika']['ipam']['config'][0]
    subnet = ipaddress.IPv4Network(default(ipam['subnet']))
    gateway = ipaddress.IPv4Address(default(ipam['gateway']))
    assert subnet == ipaddress.IPv4Network('198.18.250.0/24') and gateway == ipaddress.IPv4Address('198.18.250.1')
    assert gateway in subnet
    for private in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '192.168.65.0/24'):
        assert not subnet.overlaps(ipaddress.IPv4Network(private))
    service = data['services']['narsika']
    assert service['networks'] == ['narsika']
    # The app trusts exactly the gateway the network is created with.
    assert service['environment']['NARSIKA_DOCKER_GATEWAY'] == ipam['gateway']
    assert data['services']['bootstrap']['network_mode'] == 'none'


def test_dockerfile_installs_the_openssh_client():
    assert 'openssh-client' in (ROOT / 'Dockerfile').read_text()


def test_docker_gateway_is_the_only_extra_web_source(config):
    application = create_app({**config, 'WEB_NETWORKS': '127.0.0.0/8', 'DOCKER_GATEWAY': '198.18.250.1'})
    client = application.test_client()
    assert client.get('/healthz', environ_base={'REMOTE_ADDR': '198.18.250.1'}).status_code == 200
    blocked = client.get('/healthz', environ_base={'REMOTE_ADDR': '198.18.250.7'})
    assert blocked.status_code == 403 and blocked.json['error']['code'] == 'SOURCE_NOT_ALLOWED'
    with application.app_context():
        db.session.remove()
        db.engine.dispose()
    # Without the Compose variable the gateway gets no special treatment.
    plain = create_app({**config, 'WEB_NETWORKS': '127.0.0.0/8', 'DOCKER_GATEWAY': ''})
    assert plain.test_client().get('/healthz', environ_base={'REMOTE_ADDR': '198.18.250.1'}).status_code == 403
    with plain.app_context():
        db.session.remove()
        db.engine.dispose()
