import base64
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

KEY_LOCK=threading.Lock()
SLOTS=threading.BoundedSemaphore(8)
DEVICE_LOCKS={}
LOCK=threading.Lock()

@contextmanager
def device_lock(key):
    with LOCK:mutex=DEVICE_LOCKS.setdefault(key,threading.Lock())
    if not mutex.acquire(blocking=False):fail('This device already has an active operation.','BUSY',409)
    if not SLOTS.acquire(timeout=1):
        mutex.release();fail('Network connection limit reached. Retry shortly.','BUSY',429)
    try:yield
    finally:SLOTS.release();mutex.release()

def key_host(ip,port):
    return ip if port==22 else f'[{ip}]:{port}'

def known_path():
    return Path(current_app.config['DATA_DIR'])/'known_hosts'

def probe_key(ip,port):
    address(ip)
    try:
        with socket.create_connection((ip,port),timeout=5) as sock:
            transport=paramiko.Transport(sock)
            try:
                transport.start_client(timeout=5)
                key=transport.get_remote_server_key()
                fingerprint='SHA256:'+base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('=')
                return dict(ip_address=ip,port=port,key_type=key.get_name(),public_key=key.get_base64(),fingerprint=fingerprint),key
            finally:transport.close()
    except Exception:
        fail('Unable to retrieve the SSH host key.','UNREACHABLE',502)

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
    args=dict(device_type='cisco_ios' if device.platform=='cisco' else 'mikrotik_routeros',host=device.ip_address,
        port=device.ssh_port,username=credential.username,password=values.get('password',''),secret=values.get('enable_password',''),
        ssh_strict=True,system_host_keys=False,alt_host_keys=True,alt_key_file=str(known_path()),
        conn_timeout=6,auth_timeout=8,banner_timeout=8,timeout=12,read_timeout_override=20,allow_agent=False,use_keys=False)
    if values.get('private_key'):
        key=None
        for cls in (paramiko.Ed25519Key,paramiko.RSAKey,paramiko.ECDSAKey):
            try:key=cls.from_private_key(io.StringIO(values['private_key']),password=values.get('passphrase') or None);break
            except (paramiko.SSHException,ValueError):continue
        if key is None:fail('Private key or passphrase is invalid.','AUTH_FAILED',422)
        args.update(pkey=key,use_keys=True)
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

def command(client,cmd):
    value=client.send_command(cmd)
    if re.search(r'(?im)(^% (Invalid|Error|Incomplete|Ambiguous)|^failure:|bad command name|syntax error)',str(value)):
        fail('The device does not support the requested command.','UNSUPPORTED',502)
    return str(value)

def export_configuration(device):
    with connection(device) as client:
        if device.platform=='cisco':return command(client,'show running-config')
        version=command(client,':put [/system resource get version]')
        if not version.strip().startswith('7.'):
            # RouterOS 6 has a different explicit redaction flag.
            return command(client,'/export hide-sensitive')
        output=command(client,'/export terse')
        if '#error exporting' in output.lower():fail('RouterOS returned a partial export.','PARTIAL_EXPORT',502)
        return output
