"""Isolated fixtures only. No test sends commands to a network device."""
import copy
import time
from types import SimpleNamespace
import pytest
from app.models import db, Device, FirewallReview, OperationRun, User, Backup
from app.security import APIError, decrypt, encrypt
from app.services import firewall as fw, backups, automation
from app.services.jobs import JobManager
from conftest import device, post, signin


def snapshot(platform='mikrotik'):
    rules = fw.parse_routeros('/ip firewall filter add chain=input action=accept connection-state=established,related comment="fixture existing"') if platform=='mikrotik' else []
    acls = {'TEST': [dict(sequence=100, body='permit ip any any', raw='100 permit ip any any')]} if platform=='cisco' else {}
    state = dict(rules=rules, acls=acls, bindings=[], source_ip_hint='10.0.0.100', captured_at='2026-09-12T00:00:00Z')
    state['fingerprint']=fw.digest(state)
    return state


def intent(**extra):
    return dict(source_ip='10.0.0.100', changes=[dict(service='https', protocol='tcp', port=443,
        action='allow', source='10.0.0.100/32', destination='any', **extra)])


def fake_device(platform='mikrotik'):
    obj=SimpleNamespace(id=1, name='Fixture router', ip_address='10.0.0.1', platform=platform, ssh_port=22, credential_id=None)
    obj.public=lambda:dict(id=obj.id,name=obj.name,ip_address=obj.ip_address,platform=obj.platform,ssh_port=obj.ssh_port)
    return obj


def acknowledgements(receipt):
    return dict(checksum=receipt['checksum'], target_confirmed=True, source_confirmed=True,
                risk_ack=True, recovery_saved=True, no_rollback_ack=True)


def make_review(admin, monkeypatch, data=None, platform='mikrotik'):
    target=device(admin, platform=platform)
    baseline=snapshot(platform)
    monkeypatch.setattr(fw, 'read_state', lambda dev:copy.deepcopy(baseline))
    response=post(admin, f'/api/firewall/devices/{target["id"]}/reviews', data or intent())
    assert response.status_code==201, response.json
    return target, response.json['data'], baseline


@pytest.mark.parametrize('value', ['10.0.0.1; /system reboot', '$(id)', '::1', '300.1.2.3', None, True, {}, ['any']])
def test_network_input_rejects_injection_and_wrong_types(value):
    data=intent(); data['changes'][0]['source']=value
    with pytest.raises(APIError):fw.normalize(data, fake_device())


@pytest.mark.parametrize('field,value', [('port',0),('port',65536),('port',True),('protocol','tcp;quit'),('action','drop'),('chain','input;bad'),('position','42'),('commands',['reboot'])])
def test_rule_input_is_bounded(field,value):
    data=intent(); data['changes'][0][field]=value
    with pytest.raises(APIError):fw.normalize(data,fake_device())


def test_any_canonicalization_and_source_type():
    assert fw.cidr('0.0.0.0/0')=='any'
    data=intent();data['source_ip']=True
    with pytest.raises(APIError):fw.normalize(data,fake_device())


def test_readonly_parser_rejects_partial_and_unsupported_export():
    for raw in ('#error exporting firewall','add chain=input action=drop','syntax error'):
        with pytest.raises(APIError):fw.parse_routeros(raw)
    rows=fw.parse_routeros('# export\n/ip firewall filter add chain=input \\\n    action=drop comment="two words" disabled=yes')
    assert rows[0]['comment']=='two words' and rows[0]['disabled']


def test_cisco_counters_do_not_change_fingerprint_input():
    a=fw.parse_cisco('Extended IP access list TEST\n    10 permit tcp any any eq ssh (10 matches)')
    b=fw.parse_cisco('Extended IP access list TEST\n    10 permit tcp any any eq ssh (11 matches)')
    assert a==b
    with pytest.raises(APIError):fw.parse_cisco('Extended IP access list TEST\n    permit ip any any')


def test_management_disruption_allowed_but_strongly_classified():
    data=intent();data['changes'][0].update(service='ssh',port=22,action='block')
    plan=fw.compile_plan(data,fake_device(),snapshot(),'ADMIN')
    assert plan['risk']=='LOCKOUT' and not plan['automatic_rollback']
    assert 'disconnect' in plan['warnings'][0].lower()
    assert 'NARSIKA_APPLIED' in plan['items'][0]['commands'][0]


def test_lockout_must_be_the_last_execution_item():
    data=intent();data['changes'].insert(0,{**data['changes'][0],'service':'ssh','port':22,'action':'block'})
    with pytest.raises(APIError, match='final position'):fw.compile_plan(data,fake_device(),snapshot(),'ADMIN')


def test_duplicate_and_conflicting_intents():
    data=intent();data['changes']*=2
    with pytest.raises(APIError):fw.compile_plan(data,fake_device(),snapshot(),'ADMIN')
    data=intent();data['changes'].append({**data['changes'][0],'action':'block'})
    with pytest.raises(APIError, match='Conflicting'):fw.compile_plan(data,fake_device(),snapshot(),'ADMIN')


def test_cisco_preserves_bindings_requires_free_sequence_and_rejects_hidden_remarks():
    data=intent(acl='TEST',sequence=10); baseline=snapshot('cisco')
    plan=fw.compile_plan(data,fake_device('cisco'),baseline,'ADMIN')
    assert plan['items'][0]['commands']==['10 permit tcp host 10.0.0.100 any eq 443']
    assert plan['items'][0]['parents']==['ip access-list extended TEST']
    assert 'no 100' not in '\n'.join(plan['recovery'])
    data['changes'][0]['sequence']=100
    with pytest.raises(APIError):fw.compile_plan(data,fake_device('cisco'),baseline,'ADMIN')
    data['changes'][0]['sequence']=10;baseline['unsupported_acls']=['TEST']
    with pytest.raises(APIError):fw.compile_plan(data,fake_device('cisco'),baseline,'ADMIN')


def test_operator_cannot_disguise_custom_ports_as_presets():
    data=intent();data['changes'][0]['port']=1234
    with pytest.raises(APIError) as err:fw.compile_plan(data,fake_device(),snapshot(),'OPERATOR')
    assert err.value.status==403
    assert fw.compile_plan(intent(),fake_device(),snapshot(),'OPERATOR')['risk']=='MEDIUM'


def observed_after(plan):
    after=copy.deepcopy(plan['baseline'])
    if plan['device']['platform']=='mikrotik':
        for item in plan['items']:
            fields=item['expected']['fields']
            raw='/ip firewall filter add '+' '.join(k+'="'+v+'"' for k,v in fields.items())
            row=fw.parse_routeros(raw)[0]
            after['rules'].insert(0,row) if item['rule']['position']=='first' else after['rules'].append(row)
    else:
        for item in plan['items']:
            e=item['expected'];after['acls'][e['acl']].append(dict(sequence=e['sequence'],body=e['body'].replace('eq 443','eq https'),raw=''))
    after['fingerprint']=fw.digest(after)
    return after


@pytest.mark.parametrize('platform',['mikrotik','cisco'])
def test_configuration_verification_is_not_just_process_exit(platform):
    plan=fw.compile_plan(intent(**({'acl':'TEST','sequence':10} if platform=='cisco' else {})),fake_device(platform),snapshot(platform),'ADMIN')
    assert fw.verify(plan,observed_after(plan))[0]
    assert not fw.verify(plan,snapshot(platform))[0]


def test_verify_detects_external_changes_and_misplaced_rules():
    plan=fw.compile_plan(intent(),fake_device(),snapshot(),'ADMIN');after=observed_after(plan)
    after['rules'].reverse();assert not fw.verify(plan,after)[0]
    after=observed_after(plan);after['rules'][-1]['fields']['action']='drop'
    assert not fw.verify(plan,after)[0]


def test_view_page_and_existing_acl_route_remain(admin):
    assert admin.get('/firewall.html').status_code==200
    assert admin.get('/firewall').status_code==200
    assert b'Firewall control' in admin.get('/acl.html').data
    assert admin.get('/api/firewall/capabilities').json['data']['automatic_rollback'] is False


def test_authentication_csrf_and_no_demo_data(client,admin,app):
    assert admin.get('/api/devices').json['data']['items']==[]
    assert admin.post('/api/firewall/devices/1/refresh',json={}).status_code==403
    separate=app.test_client()
    assert separate.get('/api/firewall/capabilities').status_code==401


def test_receipt_is_encrypted_and_apply_is_idempotent(admin,app,monkeypatch):
    target,review,_=make_review(admin,monkeypatch)
    with app.app_context():
        row=db.session.get(FirewallReview,review['id'])
        assert 'commands' not in row.encrypted_plan
        assert fw.digest(decrypt(row.encrypted_plan))==row.checksum
        assert OperationRun.query.count()==0
    url='/api/firewall/reviews/'+review['id']+'/apply'
    first=post(admin,url,acknowledgements(review));assert first.status_code==202,first.json
    second=post(admin,url,acknowledgements(review));assert second.status_code==200
    assert first.json['data']['run']['id']==second.json['data']['run']['id']
    with app.app_context():assert OperationRun.query.count()==1


@pytest.mark.parametrize('missing',['target_confirmed','source_confirmed','risk_ack','recovery_saved','no_rollback_ack'])
def test_server_requires_every_confirmation(admin,monkeypatch,missing):
    _,review,_=make_review(admin,monkeypatch);body=acknowledgements(review);body[missing]='true'
    response=post(admin,'/api/firewall/reviews/'+review['id']+'/apply',body)
    assert response.status_code==422


def test_lockout_needs_typed_confirmation_and_oob(admin,monkeypatch):
    data=intent();data['changes'][0].update(service='ssh',port=22,action='block')
    target,review,_=make_review(admin,monkeypatch,data)
    url='/api/firewall/reviews/'+review['id']+'/apply';body=acknowledgements(review)
    assert post(admin,url,body).status_code==422
    body.update(oob_confirmed=True,device_name=target['name'],phrase='DISCONNECT')
    assert post(admin,url,body).status_code==202


@pytest.mark.parametrize('change',['expire','tamper','target'])
def test_invalid_receipt_never_queues(admin,app,monkeypatch,change):
    target,review,_=make_review(admin,monkeypatch)
    with app.app_context():
        row=db.session.get(FirewallReview,review['id'])
        if change=='expire':row.expires_at=time.time()-1
        elif change=='tamper':row.checksum='0'*64
        else:db.session.get(Device,target['id']).ssh_port=2222
        db.session.commit()
    assert post(admin,'/api/firewall/reviews/'+review['id']+'/apply',acknowledgements(review)).status_code==409
    with app.app_context():assert OperationRun.query.count()==0


@pytest.mark.parametrize('result',['success','partial','unreachable','stale','backup_failed'])
def test_real_job_dispatch_preserves_observed_status(admin,app,monkeypatch,result):
    _,review,baseline=make_review(admin,monkeypatch)
    response=post(admin,'/api/firewall/reviews/'+review['id']+'/apply',acknowledgements(review))
    run_id=response.json['data']['run']['id']; calls=[]
    after=observed_after(review['plan'])
    def read(dev):
        calls.append('read')
        if result=='stale':return {**baseline,'fingerprint':'changed'}
        if calls.count('read')>2:
            if result=='unreachable':raise APIError('fixture SSH loss','UNREACHABLE',502)
            if result=='partial':return {**baseline,'fingerprint':'changed'}
            return after
        return copy.deepcopy(baseline)
    def capture(dev,ident):
        calls.append('backup')
        if result=='backup_failed':raise APIError('fixture backup failed','EMPTY_BACKUP',502)
        return backups.store_backup(dev,ident,'fixture backup, not a real network configuration')
    def execute(run,params,dev,**kwargs):
        assert kwargs['lock_held'] is True;calls.append('execute')
    monkeypatch.setattr(fw,'read_state',read);monkeypatch.setattr(backups,'capture',capture);monkeypatch.setattr(automation,'run_ansible',execute)
    manager=object.__new__(JobManager);manager.app=app
    manager.execute(run_id)
    expected={'success':'SUCCESS','partial':'PARTIAL','unreachable':'APPLIED_UNVERIFIED','stale':'FAILED','backup_failed':'FAILED'}[result]
    with app.app_context():
        run=db.session.get(OperationRun,run_id);assert run.status==expected,run.output
        assert bool(Backup.query.count())==(result not in ('stale','backup_failed'))
    if result in ('stale','backup_failed'):assert 'execute' not in calls
    else:assert calls.index('backup')<calls.index('execute')
    detail=admin.get('/api/firewall/reviews/'+review['id']).json['data']
    assert detail['status']==expected


@pytest.mark.parametrize('role',['VIEWER','OPERATOR'])
def test_readonly_and_operator_roles_are_enforced_on_api(admin,app,monkeypatch,role):
    target=device(admin,platform='mikrotik')
    with app.app_context():
        user=User(username='fixture-role',name='Fixture role',role=role,must_change_password=False)
        user.set_password('fixture-role-password');db.session.add(user);db.session.commit()
    client=app.test_client();assert signin(client,'fixture-role','fixture-role-password').status_code==200
    monkeypatch.setattr(fw,'read_state',lambda dev:snapshot())
    url=f'/api/firewall/devices/{target["id"]}/reviews'
    review=post(client,url,intent());assert review.status_code==201,review.json
    data=review.json['data']
    apply=post(client,'/api/firewall/reviews/'+data['id']+'/apply',acknowledgements(data))
    assert apply.status_code==(403 if role=='VIEWER' else 202)
    high=intent();high['changes'][0]['source']='any'
    assert post(client,url,high).status_code==(201 if role=='VIEWER' else 403)


def test_production_without_worker_does_not_consume_receipt(admin,app,monkeypatch):
    _,review,_=make_review(admin,monkeypatch)
    app.config['TESTING']=False
    try:
        result=post(admin,'/api/firewall/reviews/'+review['id']+'/apply',acknowledgements(review))
        assert result.status_code==503
        with app.app_context():assert db.session.get(FirewallReview,review['id']).status=='REVIEWED'
    finally:app.config['TESTING']=True


def test_queue_failure_rolls_back_receipt_reservation(admin,app,monkeypatch):
    _,review,_=make_review(admin,monkeypatch)
    from app import firewall_api
    def full(*args,**kwargs):raise APIError('fixture queue full','BUSY',429)
    monkeypatch.setattr(firewall_api,'enqueue',full)
    response=post(admin,'/api/firewall/reviews/'+review['id']+'/apply',acknowledgements(review))
    assert response.status_code==429
    with app.app_context():
        row=db.session.get(FirewallReview,review['id']);assert row.status=='REVIEWED' and row.run_id is None


def test_concurrent_apply_never_creates_two_jobs(admin,app,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    _,review,_=make_review(admin,monkeypatch)
    cookie=admin.get_cookie('session').value
    def send():
        client=app.test_client();client.set_cookie('session',cookie)
        return post(client,'/api/firewall/reviews/'+review['id']+'/apply',acknowledgements(review)).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:send(),range(2)))
    assert 202 in results and all(x in (200,202,409) for x in results),results
    with app.app_context():assert OperationRun.query.count()==1


def test_worker_rechecks_role_changes_while_queued(admin,app,monkeypatch):
    _,review,_=make_review(admin,monkeypatch)
    response=post(admin,'/api/firewall/reviews/'+review['id']+'/apply',acknowledgements(review))
    run_id=response.json['data']['run']['id']
    with app.app_context():
        user=User.query.filter_by(username='admin').one();user.session_version+=1;db.session.commit()
    def forbidden(dev):pytest.fail('No network reads should happen after the account changes.')
    monkeypatch.setattr(fw,'read_state',forbidden)
    manager=object.__new__(JobManager);manager.app=app;manager.execute(run_id)
    with app.app_context():
        run=db.session.get(OperationRun,run_id);assert run.status=='FAILED' and run.error_code=='AUTHORIZATION_CHANGED'


def test_generic_automation_endpoint_cannot_bypass_firewall_review(admin):
    target=device(admin,platform='mikrotik')
    result=post(admin,'/api/automation/runs',dict(kind='firewall',device_id=target['id'],parameters={'commands':['bad']}))
    assert result.status_code==422


def test_retention_preserves_runs_referenced_by_firewall_receipts(admin,app,monkeypatch):
    from pathlib import Path
    from tools.maintenance import maintain
    _,review,_=make_review(admin,monkeypatch)
    result=post(admin,'/api/firewall/reviews/'+review['id']+'/apply',acknowledgements(review))
    with app.app_context():
        run=db.session.get(OperationRun,result.json['data']['run']['id'])
        run.status='FAILED';run.finished_at='2000-01-01T00:00:00Z';db.session.commit()
    preview=maintain(Path(app.config['DATA_DIR']),Path(app.config['BACKUP_DIR']),app.config['ENCRYPTION_KEY'],1)
    assert preview['records']['operation_run']==0
