"""Workspace refinement regressions; all device activity stays inside fixtures."""
import json
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace
from sqlalchemy import event
from app.models import db, User, Device, Credential, Group, OperationRun, now
from app.security import encrypt
from app.services.jobs import JobManager, enqueue, target_identity
from app.services import telemetry
from conftest import credential, device, post


def test_dispatch_preserves_target_order_without_starving_other_targets(admin,app):
    first=device(admin);second=device(admin,ip='10.0.0.2')
    scheduled=[]
    manager=SimpleNamespace(app=app,futures={},pool=SimpleNamespace(submit=lambda fn,ident:scheduled.append(ident)),execute=lambda ident:None)
    with app.app_context():
        a=enqueue('backup',first['id'],1,{});b=enqueue('backup',first['id'],1,{})
        c=enqueue('backup',second['id'],1,{});db.session.commit()
        JobManager.dispatch_pending(manager)
        assert scheduled==[a.id,c.id]
        assert b.status=='PENDING'
        a.status='SUCCESS';db.session.commit();manager.futures.pop(a.id)
        JobManager.dispatch_pending(manager)
        assert scheduled==[a.id,c.id,b.id]


def test_pending_cancellation_is_processed_with_no_free_worker(admin,app):
    target=device(admin)
    manager=SimpleNamespace(app=app,futures={100:None,101:None})
    with app.app_context():
        row=enqueue('backup',target['id'],1,{});row.cancel_requested=True;db.session.commit()
        JobManager.dispatch_pending(manager)
        assert row.status=='CANCELLED' and row.finished_at


def test_cancellation_at_execution_entry_never_contacts_device(admin,app,monkeypatch):
    target=device(admin)
    with app.app_context():
        row=enqueue('backup',target['id'],1,{});row.cancel_requested=True;db.session.commit();ident=row.id
    from app.services import backups
    monkeypatch.setattr(backups,'capture',lambda *a:(_ for _ in ()).throw(AssertionError('Network contact after cancellation')))
    JobManager.execute(SimpleNamespace(app=app),ident)
    with app.app_context():assert db.session.get(OperationRun,ident).status=='CANCELLED'


def test_busy_sample_defers_job_without_replaying_network_work(admin,app,monkeypatch):
    from app.services import backups,network
    target=device(admin,credential(admin));calls=[]
    with app.app_context():
        row=enqueue('backup',target['id'],1,{});row.status='RUNNING';db.session.commit();ident=row.id
    monkeypatch.setattr(backups,'capture',lambda *args:calls.append(args) or SimpleNamespace(id=99))
    with network.device_lock(target['id']):JobManager.execute(SimpleNamespace(app=app),ident)
    with app.app_context():
        row=db.session.get(OperationRun,ident)
        assert row.status=='PENDING' and row.started_at is None and not calls
    JobManager.execute(SimpleNamespace(app=app),ident)
    with app.app_context():assert db.session.get(OperationRun,ident).status=='SUCCESS'
    assert len(calls)==1


def test_lightweight_history_keeps_legacy_and_detail_contract(admin,app):
    target=device(admin)
    with app.app_context():
        row=enqueue('backup',target['id'],1,{});row.output='retained log\n'*3000;db.session.commit();ident=row.id
    full=admin.get('/api/automation/runs');slim=admin.get('/api/automation/runs?summary=true')
    assert 'output' in full.json['data']['items'][0]
    assert 'output' not in slim.json['data']['items'][0]
    assert len(slim.data)<len(full.data)/20
    assert admin.get(f'/api/automation/runs/{ident}').json['data']['output'].startswith('retained log')


def test_page_bootstrap_avoids_audit_and_unneeded_account_list(admin,app):
    post(admin,'/api/users',{'username':'other','role':'VIEWER'})
    full=admin.get('/api/bootstrap').json['data']
    compact=admin.get('/api/bootstrap?view=inventory').json['data']
    assert full['audit'] and len(full['users'])==2
    assert compact['audit']==[] and len(compact['users'])==1
    assert len(admin.get('/api/bootstrap?view=settings').json['data']['users'])==2
    assert admin.get('/api/bootstrap?view=invalid').status_code==422
    assert admin.get('/api/audit').json['data']['items']


def test_inventory_relationship_queries_do_not_grow_with_row_count(admin,app):
    with app.app_context():
        for i in range(30):
            group=Group(name='Group '+str(i));profile=Credential(name='Profile '+str(i),username='test',kind='ssh',encrypted_secret=encrypt({'password':'test-only'}))
            db.session.add_all([group,profile]);db.session.flush()
            db.session.add(Device(name='Device '+str(i),ip_address='10.1.0.'+str(i+1),group_id=group.id,credential_id=profile.id))
        db.session.commit();engine=db.engine
    statements=[]
    def record(conn,cursor,statement,*args):
        if statement.lstrip().upper().startswith('SELECT'):statements.append(statement)
    event.listen(engine,'before_cursor_execute',record)
    try:response=admin.get('/api/devices')
    finally:event.remove(engine,'before_cursor_execute',record)
    assert len(response.json['data']['items'])==30
    assert len(statements)<=3,statements


def test_renaming_credential_keeps_connection_identity_and_health(admin,app):
    profile=credential(admin);target=device(admin,profile)
    with app.app_context():
        row=db.session.get(Device,target['id']);before=target_identity(row)
        row.health_json={'status':'online','sampled_at':now()};db.session.commit()
    assert post(admin,f'/api/credentials/{profile["id"]}',{'name':'Renamed profile'},'PATCH').status_code==200
    with app.app_context():
        row=db.session.get(Device,target['id'])
        assert target_identity(row)==before and row.health_json['status']=='online'
    assert post(admin,f'/api/credentials/{profile["id"]}',{'username':'changed-user'},'PATCH').status_code==200
    with app.app_context():
        row=db.session.get(Device,target['id'])
        assert target_identity(row)!=before and row.health_json is None


def test_sample_cache_includes_credential_username(admin,app):
    profile=credential(admin);target=device(admin,profile);calls=[]
    @telemetry.coalesced_sample
    def read(row):calls.append(row.credential.username);return {'username':row.credential.username}
    with app.app_context():
        row=db.session.get(Device,target['id']);first=read(row)
        row.credential.username='changed-with-same-ciphertext'
        second=read(row)
        assert first!=second and len(calls)==2


def test_health_checks_the_live_dispatcher_when_required(admin,app):
    class Manager:
        def available(self):return False
    app.config['START_WORKER']=True;app.extensions['jobs']=Manager()
    try:
        assert admin.get('/healthz').status_code==503
        system=admin.get('/api/system').json['data']
        assert system['worker_available'] is False
        assert system['queue']=={'pending':0,'running':0,'capacity':32}
    finally:app.extensions.pop('jobs');app.config['START_WORKER']=False


def test_stopped_worker_refuses_new_jobs_in_deployed_mode(admin,app):
    target=device(admin,credential(admin))
    app.extensions['jobs']=SimpleNamespace(available=lambda:False)
    app.testing=False
    try:
        response=post(admin,'/api/automation/runs',{'kind':'backup','device_id':target['id']})
        assert response.status_code==503 and response.json['error']['code']=='CAPABILITY_UNAVAILABLE'
    finally:app.extensions.pop('jobs');app.testing=True


def test_refined_shell_and_setup_are_served_without_seeded_data(admin):
    response=admin.get('/index.html')
    assert b'/static/css/workspace.css' in response.data
    for ident in ('inventory-setup','refresh-inventory','inventory-updated','clear-inventory-filters'):
        assert f'id="{ident}"'.encode() in response.data
    assert admin.get('/api/devices').json['data']['items']==[]
    assert admin.get('/static/css/workspace.css').status_code==200


def test_shared_frontend_behaviors():
    root=Path(__file__).resolve().parents[1]
    subprocess.run(['node','--test','tests/frontend_core.test.cjs'],cwd=root,check=True,capture_output=True,text=True)
