"""Schedule contracts, calendar boundaries and actual queue integration; no devices contacted."""
import concurrent.futures
import io
import sqlite3
import time
from datetime import datetime,timezone
from pathlib import Path
from types import SimpleNamespace
import pytest
from cryptography.fernet import Fernet
from app import create_app
from app.models import db,ScheduledTask,ScheduleOccurrence,ScheduleRun,OperationRun,User,Playbook,Device
from app.security import APIError,decrypt,encrypt
from app.services import scheduling as S
from app.services.jobs import JobManager,enqueue
from conftest import credential,device,post,signin,token

def stamp(value):return datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()

def body(admin,**updates):
    profile=credential(admin);target=device(admin,profile)
    value={'name':'Fixture daily backup','kind':'backup','device_ids':[target['id']],'enabled':True,
           'rule':{'frequency':'daily','timezone':'UTC','start_at':'2090-01-01T02:00'}}
    value.update(updates);return value

def save(admin,data):
    response=post(admin,'/api/schedules/preview',data)
    assert response.status_code==200,response.json
    review=response.json['data']
    response=post(admin,'/api/schedules',{'review_token':review['review_token']})
    assert response.status_code==201,response.json
    return response.json['data'],review

def make_due(app,ident,when):
    with app.app_context():
        row=db.session.get(ScheduledTask,ident);row.next_due=when;db.session.commit()

@pytest.mark.parametrize('frequency,extra,expected',[
    ('once',{},'2026-09-15T02:00:00Z'),('daily',{},'2026-09-15T02:00:00Z'),
    ('weekly',{'weekdays':[0]},'2026-09-21T02:00:00Z'),('interval',{'interval_hours':6},'2026-09-15T02:00:00Z')])
def test_calendar_modes(frequency,extra,expected):
    rule=S.rule_data({'frequency':frequency,'timezone':'UTC','start_at':'2026-09-15T02:00',**extra})
    assert S.utc(S.next_time(rule,stamp('2026-09-14T12:00:00Z')))==expected

def test_tehran_and_elapsed_interval_are_explicit():
    rule=S.rule_data({'frequency':'daily','timezone':'Asia/Tehran','start_at':'2026-09-15T02:00'})
    assert S.utc(S.next_time(rule,stamp('2026-09-14T12:00:00Z')))=='2026-09-14T22:30:00Z'
    rule=S.rule_data({'frequency':'interval','timezone':'America/New_York','start_at':'2026-10-31T12:00','interval_hours':24})
    dates=S.upcoming(rule,stamp('2026-10-30T00:00:00Z'))
    assert stamp(dates[1]['utc'])-stamp(dates[0]['utc'])==86400
    assert '11:00:00' in dates[1]['local']

def test_dst_gap_is_skipped_and_fold_executes_once():
    rule=S.rule_data({'frequency':'daily','timezone':'America/New_York','start_at':'2026-03-07T02:30'})
    assert S.utc(S.next_time(rule,stamp('2026-03-07T08:00:00Z')))=='2026-03-09T06:30:00Z'
    rule=S.rule_data({'frequency':'daily','timezone':'America/New_York','start_at':'2026-10-31T01:30'})
    first=S.next_time(rule,stamp('2026-11-01T00:00:00Z'))
    assert S.utc(first)=='2026-11-01T05:30:00Z'
    assert S.utc(S.next_time(rule,first))=='2026-11-02T06:30:00Z'
    with pytest.raises(APIError):S.rule_data({'frequency':'once','timezone':'America/New_York','start_at':'2026-03-08T02:30'})

@pytest.mark.parametrize('updates',[{'timezone':'Not/AZone'},{'frequency':'cron'},{'frequency':'weekly','weekdays':[]},{'frequency':'weekly','weekdays':[True]},{'frequency':'interval','interval_hours':0},{'start_at':'bad'}])
def test_invalid_calendar_rules(updates):
    with pytest.raises(APIError):S.rule_data({'frequency':'daily','timezone':'UTC','start_at':'2090-01-01T02:00',**updates})

def test_preview_save_repeat_and_encrypted_definition(admin,app):
    data=body(admin);row,review=save(admin,data)
    assert len(review['upcoming'])==3 and row['targets'][0]['id']==data['device_ids'][0]
    repeated=post(admin,'/api/schedules',{'review_token':review['review_token']})
    assert repeated.status_code==200 and repeated.json['data']['id']==row['id']
    with app.app_context():
        stored=ScheduledTask.query.one()
        assert 'identity' not in stored.encrypted_definition
        assert decrypt(stored.encrypted_definition)['targets'][0]['identity']['credential_revision']
        assert OperationRun.query.count()==0
    assert admin.get('/schedules.html').status_code==200
    assert b'href="/schedules.html"' in admin.get('/index.html').data
    assert admin.get('/api/bootstrap?view=schedules').status_code==200

def test_preview_rejects_unsupported_operation_and_reserved_variables(admin):
    data=body(admin,kind='firewall')
    assert post(admin,'/api/schedules/preview',data).status_code==422
    books=admin.get('/api/playbooks').json['data']['items'];book=next(b for b in books if b['vendor']=='Cisco')
    data.update(kind='playbook',playbook_id=book['id'],variables={'ansible_host':'10.0.0.9'})
    assert post(admin,'/api/schedules/preview',data).status_code==422
    data.update(variables={},playbook_id=next(b for b in books if b['vendor']=='MikroTik')['id'])
    assert post(admin,'/api/schedules/preview',data).status_code==422

def test_change_between_preview_and_save_requires_fresh_review(admin,app):
    data=body(admin);review=post(admin,'/api/schedules/preview',data).json['data']
    post(admin,'/api/devices/'+str(data['device_ids'][0]),{'ip_address':'10.0.0.8'},'PATCH')
    response=post(admin,'/api/schedules',{'review_token':review['review_token']})
    assert response.status_code==409 and response.json['error']['code']=='STALE_STATE'
    with app.app_context():assert ScheduledTask.query.count()==0

def test_two_ticks_submit_each_target_once(admin,app):
    data=body(admin)
    with app.app_context():profile=SimpleNamespace(id=db.session.get(Device,data['device_ids'][0]).credential_id)
    second=device(admin,{'id':profile.id},ip='10.0.0.2');data['device_ids'].append(second['id'])
    row,_=save(admin,data);moment=time.time();make_due(app,row['id'],moment)
    def tick():
        with app.app_context():S.tick(moment)
    with concurrent.futures.ThreadPoolExecutor(2) as pool:list(pool.map(lambda _:tick(),range(2)))
    with app.app_context():
        assert ScheduleOccurrence.query.count()==1 and OperationRun.query.count()==2
        assert ScheduleRun.query.count()==2
    history=admin.get(f'/api/schedules/{row["id"]}/history').json['data']['items'][0]
    assert history['status']=='QUEUED' and history['target_count']==2

def test_missed_windows_do_not_burst(admin,app):
    row,_=save(admin,body(admin));moment=time.time();make_due(app,row['id'],moment-3*86400)
    with app.app_context():
        S.tick(moment);S.tick(moment)
        assert OperationRun.query.count()==0 and ScheduleOccurrence.query.one().status=='MISSED'
        assert db.session.get(ScheduledTask,row['id']).next_due>moment

def test_overlap_and_manual_idempotency(admin,app):
    row,_=save(admin,body(admin));url=f'/api/schedules/{row["id"]}/run';request={'request_key':'one-manual-request-1234','revision':row['revision']}
    first=post(admin,url,request);second=post(admin,url,request)
    assert first.status_code==202 and second.json['data']['reused'] is True
    assert first.json['data']['occurrence']['id']==second.json['data']['occurrence']['id']
    skipped=post(admin,url,{**request,'request_key':'another-request-key-1234'}).json['data']['occurrence']
    assert skipped['status']=='SKIPPED_OVERLAP'
    with app.app_context():assert OperationRun.query.count()==1

def test_capacity_is_all_or_none(admin,app):
    data=body(admin);target=data['device_ids'][0]
    second=device(admin,ip='10.0.0.2')
    with app.app_context():
        db.session.get(Device,second['id']).credential_id=db.session.get(Device,target).credential_id;db.session.commit()
    data['device_ids'].append(second['id']);row,_=save(admin,data)
    with app.app_context():
        for _ in range(31):enqueue('backup',target,1,{})
        db.session.commit()
    response=post(admin,f'/api/schedules/{row["id"]}/run',{'request_key':'queue-capacity-test-key','revision':1})
    assert response.json['data']['occurrence']['status']=='SKIPPED_CAPACITY'
    with app.app_context():assert OperationRun.query.count()==31 and ScheduleRun.query.count()==0

@pytest.mark.parametrize('change',['owner','credential','target','archive'])
def test_changed_authority_or_target_blocks_and_pauses(admin,app,change):
    data=body(admin);row,_=save(admin,data);moment=time.time()
    with app.app_context():
        task=db.session.get(ScheduledTask,row['id']);task.next_due=moment
        device=db.session.get(Device,data['device_ids'][0])
        if change=='owner':task.owner.role='VIEWER'
        elif change=='credential':device.credential.username='changed'
        elif change=='target':device.ip_address='10.0.0.99'
        else:device.archived_at='2026-01-01T00:00:00Z'
        db.session.commit();S.tick(moment)
        assert not task.enabled and task.attention
        assert ScheduleOccurrence.query.one().status=='BLOCKED' and OperationRun.query.count()==0

def test_pause_resume_does_not_cancel_queued_work(admin,app):
    row,_=save(admin,body(admin));url=f'/api/schedules/{row["id"]}'
    post(admin,url+'/run',{'request_key':'pause-test-request-1234','revision':1})
    assert post(admin,url+'/state',{'enabled':False,'revision':1}).status_code==200
    with app.app_context():assert OperationRun.query.one().status=='PENDING'
    assert post(admin,url+'/state',{'enabled':True,'revision':1}).status_code==200

def test_edit_revision_blocks_stale_save_and_queued_execution(admin,app):
    data=body(admin);row,_=save(admin,data);url=f'/api/schedules/{row["id"]}'
    post(admin,url+'/run',{'request_key':'revision-test-request-key','revision':1})
    updated={**data,'task_id':row['id'],'revision':1,'name':'Reviewed new name'}
    saved,review=save(admin,updated);assert saved['revision']==2
    repeated=post(admin,'/api/schedules',{'review_token':review['review_token']})
    assert repeated.status_code==200 and repeated.json['data']['revision']==2
    assert post(admin,'/api/schedules/preview',updated).status_code==409
    with app.app_context():ident=OperationRun.query.one().id
    JobManager.execute(SimpleNamespace(app=app),ident)
    with app.app_context():assert OperationRun.query.one().error_code=='SCHEDULE_CHANGED'

def test_viewer_read_operator_ownership_and_csrf(admin,app):
    data=body(admin);row,_=save(admin,data)
    for name,role in [('viewer','VIEWER'),('operator','OPERATOR')]:
        with app.app_context():
            user=User(username=name,name=name,role=role,must_change_password=False);user.set_password('test-only-password');db.session.add(user);db.session.commit()
        client=app.test_client();assert signin(client,name,'test-only-password').status_code==200
        assert client.get('/api/schedules').status_code==200
        assert 'variables' not in client.get(f'/api/schedules/{row["id"]}').json['data']
        assert post(client,f'/api/schedules/{row["id"]}/state',{'enabled':False,'revision':1}).status_code==403
        if role=='VIEWER':assert post(client,'/api/schedules/preview',data).status_code==403
        else:
            own,_=save(client,data)
            assert own['owner_id']!=row['owner_id']
            with app.app_context():
                db.session.get(User,own['owner_id']).role='VIEWER';db.session.commit()
            assert 'variables' not in client.get(f'/api/schedules/{own["id"]}').json['data']
    assert admin.post('/api/schedules/preview',json=data).status_code==403

def test_playbook_replacement_requires_review(admin,app):
    raw=b'- hosts: all\n  gather_facts: false\n  tasks: []\n'
    response=admin.post('/api/playbooks',data={'file':(io.BytesIO(raw),'schedule-test.yml'),'vendor':'Cisco'},headers={'X-CSRFToken':token(admin)})
    book=response.json['data'];data=body(admin,kind='playbook',playbook_id=book['id'],variables={'private_value':'test-only-secret'})
    row,_=save(admin,data)
    assert admin.get(f'/api/schedules/{row["id"]}').json['data']['variables']['private_value']=='test-only-secret'
    with app.app_context():
        path=Path(db.session.get(Playbook,book['id']).path);path.write_bytes(raw+b'# changed locally\n')
    result=post(admin,f'/api/schedules/{row["id"]}/run',{'request_key':'changed-playbook-request','revision':1})
    assert result.json['data']['occurrence']['status']=='BLOCKED'

def test_receipt_and_queue_rollback_together(admin,app,monkeypatch):
    row,_=save(admin,body(admin));moment=time.time();make_due(app,row['id'],moment)
    real=S.enqueue
    def crash(*args,**kwargs):real(*args,**kwargs);raise RuntimeError('simulated interruption before commit')
    monkeypatch.setattr(S,'enqueue',crash)
    with app.app_context():
        with pytest.raises(RuntimeError):S.tick(moment)
        assert OperationRun.query.count()==0 and ScheduleOccurrence.query.count()==0
        assert db.session.get(ScheduledTask,row['id']).next_due==moment
    monkeypatch.setattr(S,'enqueue',real)
    with app.app_context():S.tick(moment);assert OperationRun.query.count()==1

def test_worker_runs_persisted_schedule_without_browser(admin,app,monkeypatch):
    from app.services import backups
    row,_=save(admin,body(admin));make_due(app,row['id'],time.time())
    calls=[];monkeypatch.setattr(backups,'capture',lambda target,run:calls.append(target.id) or SimpleNamespace(id=101))
    manager=JobManager(app)
    try:
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            with app.app_context():
                run=OperationRun.query.first()
                if run and run.status=='SUCCESS':break
            time.sleep(.05)
        else:pytest.fail('Background schedule did not complete')
    finally:manager.close()
    assert len(calls)==1
    second=JobManager(app)
    try:
        with app.app_context():assert ScheduleOccurrence.query.count()==1 and OperationRun.query.count()==1
    finally:second.close()

def test_existing_install_adds_tables_after_encrypted_snapshot(admin,app,config):
    data=body(admin)
    with app.app_context():db.session.remove();db.engine.dispose()
    source=Path(config['DATA_DIR'])/'narsika.db'
    # Disposable fixture models a pre-feature database, never a deployed database.
    with sqlite3.connect(source) as connection:
        for table in ('schedule_run','schedule_occurrence','scheduled_task'):connection.execute('DROP TABLE '+table)
    second=create_app(config)
    try:
        with second.app_context():assert ScheduledTask.query.count()==0 and db.session.get(Device,data['device_ids'][0])
        snapshot=source.with_name(source.name+'.before-schedules.sqlite3.enc')
        raw=Fernet(config['ENCRYPTION_KEY'].encode()).decrypt(snapshot.read_bytes())
        assert raw.startswith(b'SQLite format 3')
    finally:
        with second.app_context():db.session.remove();db.engine.dispose()

def test_invalid_expired_or_other_session_review_cannot_save(admin,app):
    invalid=post(admin,'/api/schedules',{'review_token':'not-a-review'})
    assert invalid.status_code==409 and invalid.json['error']['code']=='INVALID_REVIEW'
    data=body(admin);review=post(admin,'/api/schedules/preview',data).json['data']
    with app.app_context():
        receipt=decrypt(review['review_token']);receipt['expires']=time.time()-1;expired=encrypt(receipt)
    assert post(admin,'/api/schedules',{'review_token':expired}).json['error']['code']=='REVIEW_EXPIRED'
    with app.app_context():
        receipt=decrypt(review['review_token']);receipt['session']+=1;stale=encrypt(receipt)
    assert post(admin,'/api/schedules',{'review_token':stale}).status_code==409
    with app.app_context():assert ScheduledTask.query.count()==0

def test_retention_preserves_runs_referenced_by_schedule_history(admin,app,config):
    from tools.maintenance import maintain
    row,_=save(admin,body(admin))
    post(admin,f'/api/schedules/{row["id"]}/run',{'request_key':'retention-schedule-run-key','revision':1})
    with app.app_context():
        run=OperationRun.query.one();run.status='SUCCESS';run.finished_at='2020-01-01T00:00:00Z';db.session.commit()
    result=maintain(Path(config['DATA_DIR']),Path(config['BACKUP_DIR']),config['ENCRYPTION_KEY'],1)
    assert result['records']['operation_run']==0

def test_occurrence_history_exposes_per_target_terminal_outcome(admin,app):
    data=body(admin);target=data['device_ids'][0]
    with app.app_context():profile=db.session.get(Device,target).credential_id
    data['device_ids'].append(device(admin,{'id':profile},ip='10.0.0.2')['id'])
    row,_=save(admin,data)
    post(admin,f'/api/schedules/{row["id"]}/run',{'request_key':'mixed-target-outcome-key','revision':1})
    with app.app_context():
        a,b=OperationRun.query.order_by(OperationRun.id).all();a.status='SUCCESS';b.status='FAILED';b.error_code='AUTH_FAILED';db.session.commit()
    result=admin.get(f'/api/schedules/{row["id"]}/history').json['data']['items'][0]
    assert result['status']=='PARTIAL' and result['success_count']==1 and result['target_count']==2
    assert all('output' not in run for run in result['runs'])
