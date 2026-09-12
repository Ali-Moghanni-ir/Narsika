import json
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace
import pytest
from cryptography.fernet import Fernet
from app import create_app
from app.models import db, Credential, Device, User, Group, Backup, Playbook, OperationRun, RunArtifact, DiscoveryCandidate
from app.security import encrypt,decrypt,APIError
from app.services.telemetry import parse_routeros,parse_cisco,calculate_rates
from app.services.automation import configuration_play,validate_operation,decode_event,run_ansible
from app.services.jobs import enqueue,JobManager
from app.services import backups
from conftest import credential,device,post


def test_telemetry_parser_keeps_unavailable_values_null():
    from app.services.telemetry import memory_bytes
    assert memory_bytes('1..0MiB') is None
    empty=parse_cisco('unknown','','')
    assert empty['cpu_percent'] is None and empty['uptime'] is None
    parsed=parse_routeros('  uptime: 2d3h\n  version: 7.18\n  free-memory: 64.0MiB\n  total-memory: 128.0MiB\n  cpu-load: 12%\n  board-name: test-fixture')
    assert parsed['cpu_percent']==12 and parsed['memory_percent']==50
    parsed=parse_cisco('test uptime is 2 days\nCisco IOS Software, Version 15.2\ncisco TEST (revision A)', 'CPU utilization for five seconds: 13%/2%; one minute: 9%;','Processor Pool Total: 1000 Used: 250 Free: 750')
    assert parsed['cpu_percent']==13 and parsed['memory_percent']==25


def test_counter_rates_reject_restart_reset_and_first_sample():
    first={'index':1,'rx_bytes':1000,'tx_bytes':2000,'discontinuity':0,'oper_status':1}
    assert calculate_rates([dict(first)],[],5,100,50)[0]['rx_bps'] is None
    current={**first,'rx_bytes':2000,'tx_bytes':4000}
    assert calculate_rates([dict(current)],[first],10,110,100)[0]['rx_bps']==800
    assert calculate_rates([dict(current)],[first],10,10,100)[0]['rx_bps'] is None
    assert calculate_rates([{**current,'discontinuity':1}],[first],10,110,100)[0]['rx_bps'] is None
    assert calculate_rates([{**current,'rx_bytes':5}],[first],10,110,100)[0]['rx_bps'] is None


def test_acl_any_icmp_and_ip_do_not_add_invalid_port_or_host(app):
    with app.app_context():
        for protocol in ('icmp','ip'):
            p=validate_operation('acl',{'name':'TEST','protocol':protocol,'action':'permit','source':'any','destination':'10.0.0.9'},SimpleNamespace(platform='cisco'))
            play=configuration_play('acl',p,SimpleNamespace(platform='cisco'))
            line=play[0]['tasks'][0]['cisco.ios.ios_config']['lines'][0]
            assert line==f'permit {protocol} any host 10.0.0.9'
            assert 'eq ' not in line and 'host any' not in line


def test_backup_encryption_integrity_and_archive(admin,app):
    row=device(admin)
    with app.app_context():
        backup=backups.store_backup(db.session.get(Device,row['id']),None,'hostname test-fixture\npassword private-test-secret\n')
        db.session.commit();ident=backup.id;path=Path(app.config['BACKUP_DIR'])/backup.path
        assert b'private-test-secret' not in path.read_bytes()
        assert backups.content(backup).startswith('hostname')
    response=admin.get(f'/api/backups/{ident}/download');assert response.status_code==200
    assert 'attachment' in response.headers['Content-Disposition']
    assert post(admin,f'/api/backups/{ident}',{},'DELETE').status_code==200
    assert path.is_file()
    with app.app_context():
        record=db.session.get(Backup,ident);record.checksum='0'*64;db.session.commit()
        with pytest.raises(APIError):backups.content(record)


def test_migration_encrypts_legacy_values_and_preserves_reversible_snapshot(config):
    path=Path(config['DATA_DIR'])/'narsika.db'
    import bcrypt
    encoded=bcrypt.hashpw(b'legacy-test-password',bcrypt.gensalt()).decode()
    with sqlite3.connect(path) as conn:
        conn.executescript('CREATE TABLE user (id INTEGER PRIMARY KEY,username VARCHAR(64) UNIQUE NOT NULL,password VARCHAR(255) NOT NULL);CREATE TABLE "group" (id INTEGER PRIMARY KEY,name VARCHAR(100) UNIQUE NOT NULL);CREATE TABLE device (id INTEGER PRIMARY KEY,name VARCHAR(100) NOT NULL,ip_address VARCHAR(50) NOT NULL,username VARCHAR(50),password VARCHAR(100),os_type VARCHAR(50),group_id INTEGER REFERENCES "group"(id));')
        conn.execute('INSERT INTO user VALUES (1,?,?)',('legacy-admin',encoded))
        conn.execute('INSERT INTO device VALUES (1,?,?,?,?,?,NULL)',('existing-router','10.0.0.9','legacy-user','plaintext-preserved','mikrotik_routeros'))
    app=create_app(config)
    with app.app_context():
        row=Device.query.one();assert row.name=='existing-router' and row.platform=='mikrotik'
        assert row.password=='' and row.username==''
        assert decrypt(row.credential.encrypted_secret)['password']=='plaintext-preserved'
        assert User.query.one().check_password('legacy-test-password')
        assert User.query.one().must_change_password
        db.session.remove();db.engine.dispose()
    snapshot=path.with_name(path.name+'.before-v2.sqlite3.enc')
    assert snapshot.is_file()
    raw=Fernet(config['ENCRYPTION_KEY'].encode()).decrypt(snapshot.read_bytes())
    assert b'plaintext-preserved' in raw
    assert b'plaintext-preserved' not in path.read_bytes()
    import subprocess,sys,os
    restored=path.parent/'restored.sqlite3'
    subprocess.run([sys.executable,'tools/restore_snapshot.py',str(snapshot),str(restored)],env={**os.environ,'NARSIKA_ENCRYPTION_KEY':config['ENCRYPTION_KEY']},check=True,capture_output=True)
    with sqlite3.connect(restored) as connection:assert connection.execute('SELECT password FROM device').fetchone()[0]=='plaintext-preserved'
    again=create_app(config)
    with again.app_context():assert Device.query.count()==1 and Credential.query.count()==1;db.session.remove();db.engine.dispose()


def test_safe_callback_decoder_never_passes_raw_output():
    assert decode_event(b'NARSIKA_EVENT []') is None
    assert decode_event(b'password=secret') is None
    assert decode_event(b'NARSIKA_EVENT '+json.dumps({'event':'ok','action':'ansible.builtin.debug','msg':'secret','changed':False}).encode())=='OK [ansible.builtin.debug]'
    assert decode_event(b'NARSIKA_EVENT {"event":"ok","action":"secret with spaces"}') is None


def test_actual_ansible_local_execution_and_private_artifacts(admin,app):
    """Executes Ansible on localhost only. Does not simulate device connectivity."""
    profile=credential(admin);target=device(admin,profile,'127.0.0.1')
    with app.app_context():
        root=Path(app.config['DATA_DIR'])/'playbooks';source=root/'local-integration.yml'
        source.write_text('''- hosts: all
  gather_facts: false
  tasks:
    - name: Assert selected-host inventory is available
      ansible.builtin.assert:
        that:
          - ansible_host == '127.0.0.1'
          - ansible_connection == 'ansible.netcommon.network_cli'
    - name: Emit a secret which must never enter the run log
      ansible.builtin.debug:
        msg: "NETWORK_SECRET_SHOULD_NEVER_BE_LOGGED"
    - name: Create local artifact root
      ansible.builtin.file:
        path: "{{ narsika_artifact_root }}"
        state: directory
        mode: '0700'
      delegate_to: localhost
      become: false
      vars:
        ansible_connection: local
        ansible_python_interpreter: "{{ ansible_playbook_python }}"
    - name: Write a local test report
      ansible.builtin.copy:
        content: "actual-local-execution"
        dest: "{{ narsika_artifact_root }}/report.txt"
        mode: '0600'
      delegate_to: localhost
      become: false
      vars:
        ansible_connection: local
        ansible_python_interpreter: "{{ ansible_playbook_python }}"
''')
        book=Playbook(name='Custom/local-integration.yml',title='Local integration test',vendor='Any',path=str(source));db.session.add(book);db.session.flush()
        run=enqueue('playbook',target['id'],1,{'playbook_id':book.id,'variables':{}});db.session.commit()
        result=run_ansible(run,{'playbook_id':book.id,'variables':{}},db.session.get(Device,target['id']))
        assert 'completed' in result
        assert 'OK [ansible.builtin.copy]' in run.output
        assert 'NETWORK_SECRET_SHOULD_NEVER_BE_LOGGED' not in run.output
        assert 'network-test-secret' not in run.output
        artifact=RunArtifact.query.filter_by(run_id=run.id).one()
        assert artifact.name=='report.txt'
        assert b'actual-local-execution' not in (Path(app.config['BACKUP_DIR'])/artifact.path).read_bytes()
        assert not list((Path(app.config['DATA_DIR'])/'runs').glob('job-*'))


def test_job_dispatcher_cancellation_restart_and_worker_lock(admin,app,monkeypatch):
    profile=credential(admin);target=device(admin,profile)
    with app.app_context():
        interrupted=enqueue('backup',target['id'],1,{});interrupted.status='RUNNING'
        cancelled=enqueue('backup',target['id'],1,{});cancelled.cancel_requested=True
        db.session.commit();interrupted_id=interrupted.id;cancelled_id=cancelled.id
    manager=JobManager(app);app.extensions['jobs']=manager
    with pytest.raises(RuntimeError,match='one Gunicorn worker'):JobManager(app)
    deadline=time.monotonic()+4
    while time.monotonic()<deadline:
        with app.app_context():
            if db.session.get(OperationRun,cancelled_id).status=='CANCELLED':break
        time.sleep(.05)
    with app.app_context():
        assert db.session.get(OperationRun,interrupted_id).status=='INTERRUPTED'
        assert db.session.get(OperationRun,cancelled_id).status=='CANCELLED'


def test_ssh_host_key_required_and_target_scope(app):
    from app.services.network import connection
    with app.app_context():
        profile=Credential(name='test',kind='ssh',username='test',encrypted_secret=encrypt({'password':'test-password'}))
        target=SimpleNamespace(ip_address='10.0.0.1',ssh_port=22,platform='cisco',credential=profile)
        with pytest.raises(APIError) as caught:
            with connection(target):pass
        assert caught.value.code=='HOST_KEY_REQUIRED'
        target.ip_address='8.8.8.8'
        with pytest.raises(APIError) as caught:
            with connection(target):pass
        assert caught.value.code=='TARGET_NOT_ALLOWED'


def test_real_worker_executes_an_api_submitted_local_playbook(admin,app):
    profile=credential(admin);target=device(admin,profile,'127.0.0.1')
    with app.app_context():
        source=Path(app.config['DATA_DIR'])/'playbooks'/'worker-test.yml'
        source.write_text('- hosts: all\n  gather_facts: false\n  tasks:\n    - ansible.builtin.assert:\n        that:\n          - ansible_host == "127.0.0.1"\n')
        book=Playbook(name='Custom/worker-test.yml',title='Worker test',vendor='Any',path=str(source));db.session.add(book);db.session.commit();book_id=book.id
    manager=JobManager(app);app.extensions['jobs']=manager
    response=post(admin,'/api/automation/runs',{'kind':'playbook','device_id':target['id'],'playbook_id':book_id,'variables':{}})
    assert response.status_code==202
    url=response.json['data']['poll_url'];deadline=time.monotonic()+12
    while time.monotonic()<deadline:
        record=admin.get(url).json['data']
        if record['status'] not in ('PENDING','RUNNING'):break
        time.sleep(.1)
    assert record['status']=='SUCCESS',record
    assert 'OK [ansible.builtin.assert]' in record['output']
    assert any(event['action']=='Run playbook' and event['result']=='success' for event in admin.get('/api/audit').json['data']['items'])
