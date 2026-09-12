"""Regressions discovered by comparing the integrated release with the legacy project."""
import hashlib
import io
import json
import os
import sqlite3
from pathlib import Path
import pytest
from cryptography.fernet import Fernet
from app import create_app
from app.models import db, User, Device, AuditLog, Credential, LoginAttempt, OperationRun, Playbook, RunArtifact
from app.security import APIError, decrypt, encrypt
from app.services.jobs import enqueue, JobManager
from app.services.automation import run_ansible
from conftest import signin,post,token,credential,device
from tools.maintenance import maintain

def test_successful_logins_do_not_lock_account_or_shared_ip(client,app):
    for _ in range(12):
        assert signin(client).status_code==200
        assert post(client,'/logout',{}).status_code==302
    with app.app_context():assert all(row.count==0 for row in LoginAttempt.query.all())

def test_success_resets_failures_and_expired_limits_are_pruned(client,app):
    for _ in range(8):assert signin(client,password='invalid-password').status_code==401
    assert signin(client).status_code==200
    post(client,'/logout',{})
    for _ in range(10):assert signin(client,password='invalid-password').status_code==401
    assert signin(client,password='invalid-password').status_code==429
    with app.app_context():
        LoginAttempt.query.update({'reset_at':0});db.session.commit()
    assert signin(client).status_code==200

def test_legacy_password_form_is_compatible(client):
    assert signin(client).status_code==200
    body={'current_password':'initial-password-for-tests','new_password':'legacy-form-new-password','confirm_password':'legacy-form-new-password','csrf_token':token(client)}
    assert client.post('/change-password',data=body).status_code==302
    post(client,'/logout',{})
    assert signin(client,password='legacy-form-new-password').status_code==200

@pytest.mark.parametrize('body',[[],['invalid'],'invalid',42])
def test_invalid_password_body_has_validation_response(admin,body):
    assert post(admin,'/change-password',body).status_code==400

def test_bootstrap_password_removed_from_environment(config,monkeypatch):
    monkeypatch.setenv('NARSIKA_ADMIN_PASSWORD','environment-password')
    monkeypatch.setenv('NARSIKA_ADMIN_PASSWORD_BASE64','c2VjcmV0')
    application=create_app(config)
    assert 'NARSIKA_ADMIN_PASSWORD' not in os.environ
    assert 'NARSIKA_ADMIN_PASSWORD_BASE64' not in os.environ
    assert application.config['ADMIN_PASSWORD']==''
    with application.app_context():db.session.remove();db.engine.dispose()

def test_untrusted_proxy_headers_do_not_change_client_identity(client,app):
    client.get('/login')
    post(client,'/login',{'username':'admin','password':'bad-password'})
    client.post('/login',json={'username':'admin','password':'bad-password'},headers={'X-CSRFToken':token(client),'X-Forwarded-For':'10.0.0.99'})
    with app.app_context():
        key=hashlib.sha256(b'ip:127.0.0.1').hexdigest()
        assert db.session.get(LoginAttempt,key).count==2

def test_explicit_proxy_setting_trusts_one_overwritten_hop(config):
    application=create_app({**config,'TRUST_PROXY_HOPS':1,'SESSION_COOKIE_SECURE':True})
    client=application.test_client()
    response=client.get('/login',headers={'X-Forwarded-For':'10.0.0.99','X-Forwarded-Proto':'https'})
    assert 'Strict-Transport-Security' in response.headers
    with application.app_context():db.session.remove();db.engine.dispose()

def test_key_mismatch_does_not_mutate_legacy_values_in_existing_installation(app,config):
    with app.app_context():
        row=Device(name='legacy-upgrade',ip_address='10.0.0.7',username='old-user',password='old-plaintext')
        db.session.add(row);db.session.commit();db.session.remove();db.engine.dispose()
    path=Path(config['DATA_DIR'])/'narsika.db';before=path.read_bytes()
    with pytest.raises(APIError):create_app({**config,'ENCRYPTION_KEY':Fernet.generate_key().decode()})
    assert path.read_bytes()==before
    with sqlite3.connect(path) as conn:assert conn.execute('SELECT password FROM device').fetchone()[0]=='old-plaintext'

def test_duplicate_legacy_inventory_fails_before_schema_changes(config):
    path=Path(config['DATA_DIR'])/'narsika.db'
    with sqlite3.connect(path) as conn:
        conn.executescript("CREATE TABLE device (id INTEGER PRIMARY KEY,ip_address TEXT);INSERT INTO device VALUES(1,'10.0.0.1'),(2,'10.0.0.1');")
    before=path.read_bytes()
    with pytest.raises(RuntimeError,match='duplicate'):create_app(config)
    assert path.read_bytes()==before

def test_existing_snapshot_and_raw_audit_are_encrypted(config):
    application=create_app(config)
    with application.app_context():
        db.session.add(AuditLog(admin_id=1,target_ip='10.0.0.1',action_type='Old operation',status='success',output='historical-private-password'))
        db.session.commit();db.session.remove();db.engine.dispose()
    source=Path(config['DATA_DIR'])/'narsika.db'
    with sqlite3.connect(source) as conn:conn.execute('DELETE FROM schema_version WHERE version=2')
    old=source.with_name(source.name+'.before-v1.sqlite3')
    with sqlite3.connect(source) as src,sqlite3.connect(old) as dst:src.backup(dst)
    second=create_app(config)
    with second.app_context():
        record=AuditLog.query.one()
        assert not record.output and decrypt(record.encrypted_output)=='historical-private-password'
        db.session.remove();db.engine.dispose()
    assert not old.exists() and old.with_name(old.name+'.enc').exists()
    assert b'historical-private-password' not in source.read_bytes()

def test_queued_target_changes_are_rejected_and_stale_job_files_removed(admin,app):
    profile=credential(admin);target=device(admin,profile)
    stale=Path(app.config['DATA_DIR'])/'runs/job-abandoned'
    stale.mkdir();(stale/'inventory.json').write_text('abandoned-secret')
    with app.app_context():
        run=enqueue('backup',target['id'],1,{});run.status='RUNNING';db.session.commit();ident=run.id
        db.session.get(Device,target['id']).ip_address='10.0.0.88';db.session.commit()
    manager=JobManager(app);app.extensions['jobs']=manager
    assert not stale.exists()
    manager.execute(ident)
    with app.app_context():
        run=db.session.get(OperationRun,ident)
        assert run.status=='FAILED' and run.error_code=='TARGET_CHANGED'

def test_failed_ansible_run_retains_its_before_change_artifact(admin,app):
    profile=credential(admin);target=device(admin,profile,'127.0.0.1')
    with app.app_context():
        source=Path(app.config['DATA_DIR'])/'playbooks/failing.yml'
        source.write_text('''- hosts: all
  gather_facts: false
  tasks:
    - ansible.builtin.file:
        path: "{{ narsika_artifact_root }}"
        state: directory
        mode: '0700'
      delegate_to: localhost
      vars:
        ansible_connection: local
        ansible_python_interpreter: "{{ ansible_playbook_python }}"
    - ansible.builtin.copy:
        dest: "{{ narsika_artifact_root }}/before.cfg"
        content: "before-change-private-data"
        mode: '0600'
      delegate_to: localhost
      vars:
        ansible_connection: local
        ansible_python_interpreter: "{{ ansible_playbook_python }}"
    - ansible.builtin.fail:
        msg: deliberately failing after backup
''')
        book=Playbook(name='Custom/failing.yml',title='Failure regression',path=str(source));db.session.add(book);db.session.flush()
        params={'playbook_id':book.id,'variables':{}}
        run=enqueue('playbook',target['id'],1,params);db.session.commit()
        with pytest.raises(APIError) as error:run_ansible(run,params,db.session.get(Device,target['id']))
        assert error.value.code=='ANSIBLE_FAILED'
        artifact=RunArtifact.query.filter_by(run_id=run.id).one()
        assert artifact.name=='before.cfg'
        raw=(Path(app.config['BACKUP_DIR'])/artifact.path).read_bytes()
        assert b'before-change-private-data' not in raw
        assert Fernet(app.config['ENCRYPTION_KEY'].encode()).decrypt(raw)==b'before-change-private-data'

def test_retention_preview_apply_archive_and_live_worker_exclusion(admin,app):
    import gzip
    from app.models import Backup,AuditEvent
    from app.services.backups import store_backup
    target=device(admin)
    with app.app_context():
        backup=store_backup(db.session.get(Device,target['id']),None,'retention-private-configuration')
        backup.archived_at='2020-01-01T00:00:00Z';backup.created_at=backup.archived_at
        db.session.add(AuditEvent(actor='admin',action='Old test event',created_at=backup.archived_at))
        db.session.commit();ident=backup.id;path=Path(app.config['BACKUP_DIR'])/backup.path
    args=(Path(app.config['DATA_DIR']),Path(app.config['BACKUP_DIR']),app.config['ENCRYPTION_KEY'],90)
    result=maintain(*args)
    assert result['records']['backup']==1 and result['archive'] is None and path.exists()
    with app.app_context():assert db.session.get(Backup,ident) is not None;db.session.remove()
    result=maintain(*args,apply=True)
    raw=Fernet(app.config['ENCRYPTION_KEY'].encode()).decrypt(Path(result['archive']).read_bytes())
    data=json.loads(gzip.decompress(raw))
    assert data['tables']['backup'][0]['id']==ident and not path.exists()
    with app.app_context():assert db.session.get(Backup,ident) is None
    manager=JobManager(app);app.extensions['jobs']=manager
    with pytest.raises(RuntimeError,match='Stop Narsika'):maintain(*args,apply=True)

def test_entrypoint_validates_existing_account_without_password_environment(config):
    import subprocess,sys
    root=Path(__file__).resolve().parents[1]
    application=create_app(config)
    with application.app_context():db.session.remove();db.engine.dispose()
    code='''import os,runpy
def execute(*args):
    assert 'NARSIKA_ADMIN_PASSWORD' not in os.environ
    assert 'NARSIKA_ADMIN_PASSWORD_BASE64' not in os.environ
    raise SystemExit(0)
os.execv=execute
runpy.run_path('app.py',run_name='__main__')
'''
    env={**os.environ,'NARSIKA_SECRET_KEY':config['SECRET_KEY'],'NARSIKA_ENCRYPTION_KEY':config['ENCRYPTION_KEY'],'NARSIKA_DATA_DIR':config['DATA_DIR'],'NARSIKA_ADMIN_PASSWORD':'bootstrap-process-test-password'}
    result=subprocess.run([sys.executable,'-c',code],cwd=root,env=env,capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stderr
    with sqlite3.connect(Path(config['DATA_DIR'])/'narsika.db') as conn:assert conn.execute('SELECT COUNT(*) FROM user').fetchone()[0]==1
