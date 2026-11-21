import base64
import errno
import hashlib
import io
import os
import re
import socket
import subprocess
import threading
import time
from contextlib import contextmanager
from pathlib import Path
import paramiko
from flask import current_app
from ..security import address, decrypt, fail, APIError

# Netmiko session types for the platforms Narsika reaches through a network CLI.
NETMIKO_DEVICE_TYPES={'cisco':'cisco_ios','mikrotik':'mikrotik_routeros'}
KEY_LOCK=threading.Lock()
SLOTS=threading.BoundedSemaphore(8)
DEVICE_LOCKS={}
LOCK=threading.Lock()

def connection_busy(message,status):
    error=APIError(message,'BUSY',status)
    error.retryable_before_network=True
    raise error

@contextmanager
def device_lock(key):
    with LOCK:mutex=DEVICE_LOCKS.setdefault(key,threading.Lock())
    if not mutex.acquire(blocking=False):connection_busy('This device already has an active operation.',409)
    if not SLOTS.acquire(timeout=1):
        mutex.release();connection_busy('Network connection limit reached. Retry shortly.',429)
    try:yield
    finally:SLOTS.release();mutex.release()

def key_host(ip,port):
    return ip if port==22 else f'[{ip}]:{port}'

def known_path():
    return Path(current_app.config['DATA_DIR'])/'known_hosts'

PROBE_TIMEOUT = 5
DOCKER_HINT = ('If Narsika runs in Docker, also make sure the device subnet does not overlap a Docker '
               'network (see NARSIKA_DOCKER_SUBNET).')


class HandshakeTimeout(Exception):
    """paramiko stopped waiting for the handshake without raising."""

    def __init__(self, banner_seen: bool):
        super().__init__('SSH negotiation timed out')
        self.banner_seen = banner_seen


def probe_failure(error: BaseException, port: int, preamble: bytes = b'') -> tuple[str, str, int]:
    """(message, code, status) naming why the SSH host key could not be read.

    preamble holds the first bytes the server sent, when any arrived before the failure.
    """
    text = str(error)
    lowered = text.lower()
    not_ssh = (f'TCP port {port} is open but did not answer as an SSH server. Check the port number.',
               'NOT_SSH', 502)
    if isinstance(error, PermissionError) or getattr(error, 'errno', None) in (errno.EACCES, errno.EPERM):
        return ('Narsika is not permitted to open network connections from this host or container '
                '(operating system permission error). Check the container network mode, a local firewall '
                'or a security policy on the Narsika host.', 'LOCAL_NETWORK_DENIED', 503)
    if isinstance(error, ConnectionRefusedError):
        return (f'The device refused TCP port {port}: no SSH service listens on this port, '
                'or a firewall rejects the connection.', 'SSH_REFUSED', 502)
    if isinstance(error, OSError) and getattr(error, 'errno', None) in (errno.ENETUNREACH, errno.EHOSTUNREACH):
        return ('No route to the device from the Narsika host. Check routing and VLANs. '
                + DOCKER_HINT, 'NO_ROUTE', 502)
    if isinstance(error, (socket.timeout, TimeoutError)):
        return (f'TCP port {port} did not answer within {PROBE_TIMEOUT} seconds. A firewall may drop the '
                'connection, the port may be wrong, or there is no return route. ' + DOCKER_HINT,
                'SSH_TIMEOUT', 502)
    if isinstance(error, HandshakeTimeout):
        if not error.banner_seen:
            return (f'TCP port {port} accepted the connection but sent no SSH banner within '
                    f'{PROBE_TIMEOUT} seconds. Check that the port belongs to the SSH service.',
                    'NOT_SSH', 502)
        return (f'The SSH handshake did not complete within {PROBE_TIMEOUT} seconds.',
                'SSH_HANDSHAKE_FAILED', 502)
    if isinstance(error, paramiko.ssh_exception.IncompatiblePeer):
        if 'version' in lowered:
            return (f'The device speaks an unsupported SSH protocol version ({text}). Enable SSH version 2.',
                    'SSH_ALGORITHM_UNSUPPORTED', 502)
        if 'host key' in lowered:
            detail = ('Narsika accepts ssh-ed25519, ECDSA and RSA host keys, including legacy ssh-rsa. '
                      'Generate one of these host keys on the device.')
        else:
            detail = 'Update the device SSH settings or firmware to offer a current key exchange or cipher.'
        return (f'The SSH handshake failed: the device offers no algorithm Narsika accepts ({text}). ' + detail,
                'SSH_ALGORITHM_UNSUPPORTED', 502)
    # A partial read such as b'SS' may still be the start of an SSH banner.
    if preamble and preamble[:4] != b'SSH-'[:len(preamble[:4])] and (
            'protocol banner' in lowered or 'protocol version' in lowered or isinstance(error, EOFError)):
        # The service answered with something other than an SSH banner, such as HTTP or Telnet.
        return not_ssh
    if 'protocol banner' in lowered:
        cause = error.__context__
        if isinstance(cause, (socket.timeout, TimeoutError)):
            return (f'TCP port {port} accepted the connection but sent no SSH banner within '
                    f'{PROBE_TIMEOUT} seconds. Check that the port belongs to the SSH service.',
                    'NOT_SSH', 502)
        return ('The device accepted the TCP connection, then closed it before the SSH handshake. '
                'The SSH service may only allow certain source addresses (for example RouterOS '
                '/ip service available-from), or it limits concurrent sessions.',
                'SSH_HANDSHAKE_FAILED', 502)
    if 'protocol version' in lowered or 'invalid ssh banner' in lowered:
        return not_ssh
    if isinstance(error, (EOFError, ConnectionResetError)):
        return ('The device closed the connection during the SSH handshake. The SSH service may only '
                'allow certain source addresses, or it limits concurrent sessions.',
                'SSH_HANDSHAKE_FAILED', 502)
    if isinstance(error, paramiko.SSHException):
        return (f'The SSH handshake failed: {text or type(error).__name__}.', 'SSH_HANDSHAKE_FAILED', 502)
    if isinstance(error, OSError):
        return (f'The connection to TCP port {port} failed: {error.strerror or text or type(error).__name__}.',
                'UNREACHABLE', 502)
    return ('Unable to retrieve the SSH host key.', 'UNREACHABLE', 502)


def probe_key(ip, port):
    """Read the device's SSH host key without trusting it."""
    address(ip)
    preamble = b''
    try:
        with socket.create_connection((ip, port), timeout=PROBE_TIMEOUT) as sock:
            try:
                # Look at the first bytes without consuming them, to tell a non-SSH service apart.
                preamble = sock.recv(8, socket.MSG_PEEK)
            except (socket.timeout, TimeoutError):
                raise HandshakeTimeout(banner_seen=False) from None
            transport = paramiko.Transport(sock)
            transport.banner_timeout = PROBE_TIMEOUT
            try:
                transport.start_client(timeout=PROBE_TIMEOUT)
                if transport.host_key is None:
                    raise HandshakeTimeout(bool(transport.remote_version))
                key = transport.get_remote_server_key()
            finally:
                transport.close()
    except Exception as error:
        message, code, status = probe_failure(error, port, preamble)
        raise APIError(message, code, status) from None
    digest = base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('=')
    record = dict(ip_address=ip, port=port, key_type=key.get_name(), public_key=key.get_base64(),
                  fingerprint='SHA256:' + digest)
    return record, key


def trusted_key_types(ip: str, port: int) -> set[str]:
    """Key types Narsika has pinned for this address and port."""
    with KEY_LOCK:
        keys = paramiko.HostKeys(str(known_path()))
    entry = keys.lookup(key_host(ip, port))
    return set(entry.keys()) if entry else set()


def legacy_rsa_only(ip: str, port: int) -> bool:
    """True when the only pinned host key is RSA, so SHA-1 ssh-rsa signatures may be needed."""
    return trusted_key_types(ip, port) == {'ssh-rsa'}

def trust_key(ip,port,fingerprint):
    record,key=probe_key(ip,port)
    if record['fingerprint']!=fingerprint:fail('Host fingerprint changed. Verify again.','HOST_KEY_CHANGED',409)
    with KEY_LOCK:
        keys=paramiko.HostKeys(str(known_path()))
        host=key_host(ip,port)
        if host in keys and not keys.check(host,key):fail('A different host key is already trusted. Review key rotation on the server.','HOST_KEY_CHANGED',409)
        keys.add(host,key.get_name(),key)
        temporary=known_path().with_suffix('.new')
        keys.save(str(temporary));temporary.chmod(0o600);os.replace(temporary,known_path())
    return record

def ping(ip):
    try:
        result=subprocess.run(['ping','-n','-c','1','-W','1',ip],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=2)
        return result.returncode==0
    except (OSError,subprocess.TimeoutExpired):return None

def secret(profile):
    if not profile or profile.kind!='ssh':fail('Assign an SSH credential profile first.','CREDENTIAL_REQUIRED',422)
    return decrypt(profile.encrypted_secret)

def private_key(values: dict) -> paramiko.PKey:
    """Load a stored SSH private key with its optional passphrase."""
    for cls in (paramiko.Ed25519Key,paramiko.RSAKey,paramiko.ECDSAKey):
        try:
            return cls.from_private_key(io.StringIO(values['private_key']),password=values.get('passphrase') or None)
        except (paramiko.SSHException,ValueError):
            continue
    fail('Private key or passphrase is invalid.','AUTH_FAILED',422)

@contextmanager
def connection(device, profile=None, enable=True):
    from netmiko import ConnectHandler
    from netmiko.exceptions import NetmikoAuthenticationException,NetmikoTimeoutException
    address(device.ip_address)
    credential=profile or device.credential
    values=secret(credential)
    host=key_host(device.ip_address,device.ssh_port)
    if host not in paramiko.HostKeys(str(known_path())):
        fail('Verify and trust the SSH host key from the device actions menu.','HOST_KEY_REQUIRED',409)
    if device.platform not in NETMIKO_DEVICE_TYPES:
        fail('This platform is not reached through a network CLI session.','UNSUPPORTED',422)
    args=dict(device_type=NETMIKO_DEVICE_TYPES[device.platform],host=device.ip_address,
        port=device.ssh_port,username=credential.username,password=values.get('password',''),secret=values.get('enable_password',''),
        ssh_strict=True,system_host_keys=False,alt_host_keys=True,alt_key_file=str(known_path()),
        conn_timeout=6,auth_timeout=8,banner_timeout=8,timeout=12,read_timeout_override=20,allow_agent=False,use_keys=False)
    if values.get('private_key'):
        args.update(pkey=private_key(values),use_keys=True)
    client=None
    try:
        client=ConnectHandler(**args)
        if device.platform=='cisco' and enable and values.get('enable_password'):client.enable()
        yield client
    except NetmikoAuthenticationException:fail('Device authentication failed.','AUTH_FAILED',502)
    except NetmikoTimeoutException:fail('Device connection timed out.','TIMEOUT',504)
    except paramiko.BadHostKeyException:fail('SSH host key does not match the trusted key.','HOST_KEY_CHANGED',409)
    except APIError:raise
    except Exception:fail('The device did not return a usable SSH response.','DEVICE_ERROR',502)
    finally:
        if client:
            try:client.disconnect()
            except Exception:pass

# Device errors start a line. Interface descriptions are indented and RouterOS comments
# are quoted, so anchoring avoids failing a configuration dump that merely mentions them.
COMMAND_ERROR = re.compile(r'(?im)^(% (Invalid|Error|Incomplete|Ambiguous)|failure:|[ \t]*bad command name\b|[ \t]*syntax error\b)')


def command(client,cmd):
    value=client.send_command(cmd)
    if COMMAND_ERROR.search(str(value)):
        fail('The device does not support the requested command.','UNSUPPORTED',502)
    return str(value)

def export_configuration(device):
    """The platform driver's configuration backup text."""
    from . import platforms
    platforms.require(device,platforms.BACKUP)
    return platforms.driver(device).export_configuration(device)
