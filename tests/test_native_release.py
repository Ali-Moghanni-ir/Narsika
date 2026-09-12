"""Native release contracts; no privileged host changes or real device writes."""
import io
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import threading
import time
import pytest
from cryptography.fernet import Fernet
from app import create_app
from app.models import db, User, Device, OperationRun, RunArtifact
from app.security import APIError
from app.services.jobs import enqueue
from app.services import telemetry
from tools.install_native import management_networks, firewall_commands
from tools.terminal_test import run_terminal
from conftest import post, token, device, credential, signin

ROOT=Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('value', ['0.0.0.0/0', '10.0.0.3/24', '::/0', 'bad', ''])
def test_management_scope_rejects_unsafe_or_invalid_input(value):
    with pytest.raises(ValueError):management_networks(value)


def test_firewall_is_scoped_without_reset_or_interface_restriction():
    networks=management_networks('10.1.2.0/24,192.168.50.0/24')
    commands=firewall_commands(networks,8123)
    assert commands[0][3]=='deny'
    assert commands[-1][3]=='allow'
    assert all('8123' in c and 'reset' not in c and 'delete' not in c and 'on' not in c for c in commands)
    assert all(c[c.index('to')+1]=='any' for c in commands)


def test_native_is_default_and_docker_remains_explicit():
    launcher=(ROOT/'run_linux.sh').read_text()
    assert '--docker)' in launcher and launcher.rstrip().endswith('exec bash tools/run_native_linux.sh "$@"')
    unit=(ROOT/'deploy/narsika.service').read_text()
    assert 'User=narsika' in unit and 'NoNewPrivileges=true' in unit
    assert 'ReadWritePaths=/var/lib/narsika' in unit
    assert 'NARSIKA_ADMIN_PASSWORD' not in unit


def test_config_has_keys_but_no_bootstrap_secret(tmp_path):
    config=tmp_path/'narsika.env'
    subprocess.run([sys.executable,ROOT/'configure.py','--destination',config,
                    '--web-networks','10.1.0.0/16','--port','8123'],check=True,capture_output=True)
    content=config.read_text()
    assert 'NARSIKA_ADMIN_PASSWORD' not in content
    assert 'NARSIKA_BIND_ADDRESS=0.0.0.0' in content
    assert 'NARSIKA_PORT=8123' in content
    assert config.stat().st_mode & 0o077 == 0


def test_bootstrap_requires_terminal_preserves_accounts_and_forces_change(tmp_path):
    env={**os.environ,'NARSIKA_DATA_DIR':str(tmp_path),
         'NARSIKA_ENV_FILE':str(tmp_path/'absent.env'),
         'NARSIKA_SECRET_KEY':'x'*48,'NARSIKA_ENCRYPTION_KEY':Fernet.generate_key().decode(),
         'NARSIKA_START_WORKER':'false'}
    result=subprocess.run([sys.executable,ROOT/'tools/bootstrap.py'],env=env,capture_output=True,text=True)
    assert result.returncode!=0 and 'interactive terminal' in result.stderr
    output=run_terminal([sys.executable,str(ROOT/'tools/bootstrap.py')],env=env)
    match=re.search(r'Temporary password \(shown once\): ([A-Za-z0-9_-]+)',output)
    assert match is not None
    password=match.group(1)
    with sqlite3.connect(tmp_path/'narsika.db') as conn:
        row=conn.execute('SELECT password_hash,must_change_password,password FROM user').fetchone()
    assert row[1]==1 and row[2]==''
    assert password.encode() not in (tmp_path/'narsika.db').read_bytes()
    result=subprocess.run([sys.executable,ROOT/'tools/bootstrap.py'],env=env,capture_output=True,text=True)
    assert result.returncode==0 and 'preserved' in result.stdout
    assert password not in result.stdout
    with sqlite3.connect(tmp_path/'narsika.db') as conn:
        assert conn.execute('SELECT password_hash FROM user').fetchone()[0]==row[0]


def test_new_admin_password_is_never_accepted_from_environment(config,monkeypatch):
    monkeypatch.setenv('NARSIKA_ADMIN_PASSWORD','unapproved-environment-password')
    config.pop('ADMIN_PASSWORD')
    with pytest.raises(RuntimeError,match='No administrator exists'):create_app(config)


def test_web_scope_does_not_trust_forwarded_source(config):
    application=create_app({**config,'WEB_NETWORKS':'127.0.0.0/8,10.2.0.0/16'})
    client=application.test_client()
    assert client.get('/healthz').status_code==200
    blocked=client.get('/healthz',environ_base={'REMOTE_ADDR':'198.51.100.8'},headers={'X-Forwarded-For':'10.2.0.1'})
    assert blocked.status_code==403 and blocked.json['error']['code']=='SOURCE_NOT_ALLOWED'
    assert blocked.headers['X-Request-ID']==blocked.json['error']['request_id']
    with application.app_context():db.session.remove();db.engine.dispose()


def test_random_panel_password_is_returned_once_and_role_enforced(admin,app):
    result=post(admin,'/api/users',{'username':'new-operator','role':'OPERATOR'}).json['data']
    password=result.pop('temporary_password')
    assert len(password)>=24 and result['must_change_password']
    assert password not in admin.get('/api/users').get_data(as_text=True)
    client=app.test_client()
    assert signin(client,'new-operator',password).json['data']['redirect']=='/change-password'
    assert client.get('/api/devices').json['error']['code']=='PASSWORD_CHANGE_REQUIRED'
    assert post(client,'/change-password',{'current':password,'password':'new-long-password','confirm':'different'}).status_code==422
    assert post(client,'/change-password',{'current':password,'password':'new-long-password','confirm':'new-long-password'}).status_code==200
    upload=client.post('/api/playbooks',data={'file':(io.BytesIO(b'- hosts: all\n  tasks: []\n'),'custom.yml')},headers={'X-CSRFToken':token(client)})
    assert upload.status_code==403
    reset=post(admin,'/api/users/'+str(result['id']),{'reset_password':True},'PATCH').json['data']
    assert reset['temporary_password']!=password
    assert client.get('/api/devices').status_code==401


def test_upload_rejects_broken_yaml_but_allows_trusted_tasks(admin):
    def upload(raw):
        return admin.post('/api/playbooks',data={'file':(io.BytesIO(raw),'trusted.yml')},headers={'X-CSRFToken':token(admin)})
    assert upload(b'- hosts: [bad').status_code==422
    assert upload(b'a: 1').status_code==422
    assert upload(b'- hosts: all\n  tasks:\n    - ansible.builtin.command: uptime\n').status_code==201


def test_queue_capacity_reservation_is_atomic(admin,app):
    target=device(admin)
    with app.app_context():
        for _ in range(32):
            enqueue('backup',target['id'],1,{})
            db.session.commit()
        with pytest.raises(APIError) as error:enqueue('backup',target['id'],1,{})
        assert error.value.code=='BUSY'
        assert OperationRun.query.count()==32


def test_sampling_coalesces_real_results_and_invalidates_credentials(admin,app):
    target=device(admin,credential(admin))
    calls=[]
    @telemetry.coalesced_sample
    def sample(row):
        calls.append(row.id)
        time.sleep(.1)
        return {'sampled_at':'actual-fixture-timestamp','value':7}
    results=[]
    def read():
        with app.app_context():results.append(sample(db.session.get(Device,target['id'])))
    threads=[threading.Thread(target=read) for _ in range(4)]
    for t in threads:t.start()
    for t in threads:t.join()
    assert len(calls)==1 and len(results)==4
    with app.app_context():
        row=db.session.get(Device,target['id'])
        row.ssh_port=2222
        assert sample(row)['value']==7
    assert len(calls)==2


def test_artifact_limits_report_failure_without_losing_accepted_files(admin,app,tmp_path):
    from app.services.automation import collect_artifacts
    target=device(admin)
    artifacts=tmp_path/'artifacts';artifacts.mkdir()
    (artifacts/'ok.txt').write_text('real output')
    with (artifacts/'oversize.bin').open('wb') as stream:stream.truncate(5*1024*1024+1)
    with app.app_context():
        run=enqueue('backup',target['id'],1,{});db.session.commit()
        with pytest.raises(APIError) as error:collect_artifacts(tmp_path,run,db.session.get(Device,target['id']))
        assert error.value.code=='ARTIFACT_LIMIT'
        assert RunArtifact.query.filter_by(run_id=run.id).one().name=='ok.txt'


@pytest.mark.parametrize('failure',['health','firewall'])
@pytest.mark.parametrize('enabled',[True,False])
def test_native_failed_upgrade_restores_code_config_and_preserves_data(tmp_path,monkeypatch,failure,enabled):
    from types import SimpleNamespace
    from tools import install_native as installer
    base=tmp_path/'opt';base.mkdir()
    previous=base/'releases/previous';previous.mkdir(parents=True)
    (base/'current').symlink_to(previous)
    data=tmp_path/'data';data.mkdir();(data/'narsika.db').write_bytes(b'preserve-existing-data')
    config=tmp_path/'narsika.env'
    original='NARSIKA_PORT=8123\nNARSIKA_WEB_NETWORKS=10.1.0.0/16\nNARSIKA_DATA_DIR='+str(data)+'\nNARSIKA_BACKUP_DIR='+str(data/'backups')+'\n'
    config.write_text(original)
    unit=tmp_path/'narsika.service';unit.write_text('original service definition')
    admin_command=tmp_path/'narsika-admin';admin_command.write_text('original admin command')
    monkeypatch.setattr(installer,'BASE',base)
    monkeypatch.setattr(installer,'DATA',data)
    monkeypatch.setattr(installer,'CONFIG',config)
    monkeypatch.setattr(installer,'UNIT',unit)
    monkeypatch.setattr(installer,'ADMIN_COMMAND',admin_command)
    monkeypatch.setattr(installer,'release_files',lambda root:[ROOT/'configure.py',ROOT/'deploy/narsika.service'])
    monkeypatch.setattr(installer.pwd,'getpwnam',lambda name:SimpleNamespace(pw_uid=12345,pw_gid=12345,pw_shell='/usr/sbin/nologin'))
    monkeypatch.setattr(installer.grp,'getgrall',lambda:[])
    monkeypatch.setattr(installer.grp,'getgrgid',lambda gid:SimpleNamespace(gr_name='narsika',gr_gid=gid,gr_mem=[]))
    monkeypatch.setattr(installer.pwd,'getpwall',lambda:[])
    monkeypatch.setattr(installer.os,'chown',lambda *args:None)
    monkeypatch.setattr(installer.shutil,'disk_usage',lambda path:SimpleNamespace(free=4*1024**3))
    answers=iter(['8123','10.2.0.0/16','n'])
    monkeypatch.setattr('builtins.input',lambda prompt:next(answers))
    commands=[]
    def command(args,**kwargs):
        commands.append([str(a) for a in args])
        if args[0]=='ufw' and args[1]=='insert' and failure=='firewall':
            raise subprocess.CalledProcessError(1,args)
        if args[:2]==['systemctl','is-enabled']:
            return SimpleNamespace(returncode=0 if enabled else 1,stdout='')
        return SimpleNamespace(returncode=0,stdout='Status: active\n' if args[:2]==['ufw','status'] else '')
    monkeypatch.setattr(installer.subprocess,'run',command)
    def health(port):
        if failure=='health':raise RuntimeError('controlled health failure')
    monkeypatch.setattr(installer,'health',health)
    with pytest.raises((RuntimeError,subprocess.CalledProcessError)):installer.install()
    assert (base/'current').resolve()==previous
    assert config.read_text()==original
    assert unit.read_text()=='original service definition'
    assert admin_command.read_text()=='original admin command'
    assert (['systemctl','disable','narsika'] in commands) is not enabled
    assert (data/'narsika.db').read_bytes()==b'preserve-existing-data'
    assert len(list((base/'releases').iterdir()))==2
    assert commands[-1]==['systemctl','start','narsika']
