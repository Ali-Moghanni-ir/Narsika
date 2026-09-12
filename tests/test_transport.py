"""Cryptographic transport tests run against an ephemeral loopback SSH server only."""
import socket
import subprocess
import sys
import os
import threading
from pathlib import Path
import pytest
import paramiko
from pylibsshext.session import Session
from app.services.network import probe_key,trust_key,known_path,key_host
from app.security import APIError

@pytest.fixture
def ssh_server():
    key=paramiko.RSAKey.generate(2048)
    listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(8);listener.settimeout(.2)
    stop=threading.Event();active=[]
    class Server(paramiko.ServerInterface):
        def check_auth_password(self,username,password):return paramiko.AUTH_SUCCESSFUL if username=='transport-test' and password=='transport-password' else paramiko.AUTH_FAILED
        def get_allowed_auths(self,username):return 'password'
        def check_channel_request(self,kind,ident):return paramiko.OPEN_SUCCEEDED if kind=='session' else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED
        def check_channel_pty_request(self,*args):return True
        def check_channel_shell_request(self,channel):return True
    def handle(connection):
        transport=paramiko.Transport(connection);active.append(transport)
        try:
            transport.add_server_key(key);transport.start_server(server=Server())
            channel=transport.accept(5)
            if channel:
                channel.settimeout(.2);channel.send(b'\r\ntransport-fixture#');buffer=''
                while transport.is_active() and not stop.is_set():
                    try:chunk=channel.recv(4096)
                    except socket.timeout:continue
                    if not chunk:break
                    buffer+=chunk.decode(errors='ignore').replace('\r','\n')
                    while '\n' in buffer:
                        command,buffer=buffer.split('\n',1);command=command.strip()
                        output='Cisco IOS Software, Integration fixture Version 15.2\r\ntransport-fixture uptime is 1 day' if command=='show version' else ''
                        channel.send((command+'\r\n'+output+'\r\ntransport-fixture#').encode())
        except (EOFError,OSError,paramiko.SSHException):pass
        finally:transport.close()
    def loop():
        while not stop.is_set():
            try:connection,_=listener.accept()
            except socket.timeout:continue
            except OSError:break
            threading.Thread(target=handle,args=(connection,),daemon=True).start()
    thread=threading.Thread(target=loop,daemon=True);thread.start()
    yield listener.getsockname()[1],key
    stop.set();listener.close()
    for transport in active:transport.close()
    thread.join(timeout=2)


def test_fingerprint_verification_and_libssh_use_the_same_known_hosts(app,ssh_server):
    port,key=ssh_server
    with app.app_context():
        record,_=probe_key('127.0.0.1',port)
        assert record['fingerprint'].startswith('SHA256:')
        with pytest.raises(APIError) as rejected:trust_key('127.0.0.1',port,'SHA256:wrong')
        assert rejected.value.code=='HOST_KEY_CHANGED'
        config=Path(app.config['DATA_DIR'])/'transport-ssh.conf'
        config.write_text('Host *\n  StrictHostKeyChecking yes\n  UserKnownHostsFile '+str(known_path())+'\n')
        code="""import os
from pylibsshext.session import Session
client=Session()
client.connect(host='127.0.0.1',port=int(os.environ['TRANSPORT_TEST_PORT']),user='transport-test',password='transport-password',look_for_keys=False,host_key_checking=True,knownhosts=os.environ['TRANSPORT_TEST_KNOWN_HOSTS'],timeout=3)
client.close()
"""
        env={**os.environ,'TRANSPORT_TEST_PORT':str(port),'TRANSPORT_TEST_KNOWN_HOSTS':str(known_path())}
        # libssh may retain the interpreter lock while connecting; isolate it from the Python SSH server.
        rejected=subprocess.run([sys.executable,'-c',code],env=env,capture_output=True,text=True,timeout=10)
        assert rejected.returncode!=0
        assert 'Timeout' not in rejected.stderr,rejected.stderr
        trust_key('127.0.0.1',port,record['fingerprint'])
        assert paramiko.HostKeys(str(known_path())).check(key_host('127.0.0.1',port),key)
        accepted=subprocess.run([sys.executable,'-c',code],env=env,capture_output=True,text=True,timeout=10)
        assert accepted.returncode==0,accepted.stderr


def test_ansible_network_cli_accepts_trusted_loopback_host(admin,app,ssh_server,monkeypatch):
    from app.models import db,Device,Credential,Playbook
    from app.security import encrypt
    from app.services.jobs import enqueue
    from app.services.automation import run_ansible
    port,_=ssh_server
    try:
        control=socket.socket(socket.AF_UNIX);control.close()
    except PermissionError:
        pytest.skip('This execution environment prohibits Unix sockets required by Ansible network_cli.')
    with app.app_context():
        record,_=probe_key('127.0.0.1',port);trust_key('127.0.0.1',port,record['fingerprint'])
        profile=Credential(name='Transport integration',username='transport-test',kind='ssh',encrypted_secret=encrypt({'password':'transport-password'}))
        db.session.add(profile);db.session.flush()
        target=Device(name='Loopback SSH fixture',ip_address='127.0.0.1',platform='cisco',ssh_port=port,credential_id=profile.id)
        db.session.add(target);db.session.flush()
        source=Path(app.config['DATA_DIR'])/'playbooks'/'ssh-integration.yml'
        source.write_text('- hosts: all\n  gather_facts: false\n  tasks:\n    - cisco.ios.ios_command:\n        commands: show version\n      register: observed\n    - ansible.builtin.assert:\n        that:\n          - observed.stdout[0] is search("Cisco IOS")\n')
        book=Playbook(name='Custom/ssh-integration.yml',title='SSH integration',vendor='Cisco',path=str(source))
        db.session.add(book);db.session.flush()
        params={'playbook_id':book.id,'variables':{}}
        run=enqueue('playbook',target.id,1,params);db.session.commit()
        result=run_ansible(run,params,target)
        assert 'completed' in result
        assert 'OK [cisco.ios.ios_command]' in run.output
