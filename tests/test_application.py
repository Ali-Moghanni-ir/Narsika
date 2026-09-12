import io
import json
import re
from pathlib import Path
import pytest
from app import create_app
from app.models import db, User, Device, Credential, Playbook, AuditEvent, OperationRun, Backup
from app.security import decrypt
from conftest import post,signin,token,credential,device


def test_empty_installation_and_authentication(client,app):
    assert client.get('/').status_code==302
    assert client.get('/api/devices').status_code==401
    assert client.post('/login',json={}).status_code==403
    assert signin(client).status_code==200
    assert client.get('/api/devices').json['error']['code']=='PASSWORD_CHANGE_REQUIRED'
    with app.app_context():
        assert Device.query.count()==0
        assert Credential.query.count()==0
        assert Backup.query.count()==0
        assert Playbook.query.count()==16
        assert User.query.one().password!='admin'


def test_real_session_password_and_logout(admin,app):
    data=admin.get('/api/bootstrap').json['data']
    assert data['devices']==[] and data['credentials']==[]
    assert data['user']['role']=='ADMIN'
    assert 'internal_encryption_check' not in data['settings']
    response=admin.get('/logout')
    assert response.status_code==200
    assert admin.get('/api/session').json['data']['user'] is not None
    assert post(admin,'/logout',{}).status_code==302
    assert admin.get('/api/devices').status_code==401

@pytest.mark.parametrize('page',['/','/index.html','/discovery.html','/health.html','/interfaces.html','/playbooks.html','/vlan.html','/acl.html','/backups.html','/audit.html','/settings.html','/change-password.html','/logs','/playbook-runner','/health/1'])
def test_pages_render_with_real_bootstrap(admin,page):
    response=admin.get(page,follow_redirects=True)
    assert response.status_code==200
    html=response.get_data(as_text=True)
    assert 'csrf-token' in html
    assert 'Alex Morgan' not in html and 'Synthetic data' not in html and 'Local simulation' not in html
    assert 'Content-Security-Policy' in response.headers
    for path in re.findall(r'(?:src|href)="(/static/[^"?]+)',html):assert admin.get(path).status_code==200,path


def test_encrypted_credentials_device_crud_and_persistence(admin,app,config):
    profile=credential(admin)
    assert 'secret' not in json.dumps(profile) and 'password' not in profile
    row=device(admin,profile)
    assert row['health'] is None
    with app.app_context():
        saved=Credential.query.one()
        assert 'network-test-secret' not in saved.encrypted_secret
        assert decrypt(saved.encrypted_secret)['password']=='network-test-secret'
        assert Device.query.one().password==''
    assert 'network-test-secret' not in admin.get('/api/bootstrap').get_data(as_text=True)
    assert post(admin,'/api/devices',{'name':'duplicate','ip_address':'10.0.0.1','platform':'cisco'}).status_code==409
    assert post(admin,'/api/devices/'+str(row['id']),{'notes':'Saved note'},'PATCH').status_code==200
    second=create_app(config)
    with second.app_context():assert Device.query.one().notes=='Saved note';db.session.remove();db.engine.dispose()
    assert post(admin,'/api/devices/'+str(row['id']),{},'DELETE').status_code==200
    assert admin.get('/api/devices').json['data']['items']==[]
    with app.app_context():assert Device.query.count()==1
    assert post(admin,'/api/devices/'+str(row['id'])+'/restore',{}).status_code==200

@pytest.mark.parametrize('body',[
    {'name':'x','ip_address':'8.8.8.8','platform':'cisco'},
    {'name':'x','ip_address':'10.0.0.1;whoami','platform':'cisco'},
    {'name':'x','ip_address':'10.0.0.1','platform':'unknown'},
    {'name':'x','ip_address':'10.0.0.1','platform':'cisco','ssh_port':True},
    {'name':'x','ip_address':'10.0.0.1','platform':'cisco','unexpected':1}])
def test_device_validation(admin,body):assert post(admin,'/api/devices',body).status_code in (403,422)

@pytest.mark.parametrize('role',['VIEWER','OPERATOR'])
def test_server_enforced_roles(admin,app,role):
    assert post(admin,'/api/users',{'username':'limited','name':'Limited','role':role,'password':'limited-test-password'}).status_code==201
    with app.app_context():User.query.filter_by(username='limited').one().must_change_password=False;db.session.commit()
    other=app.test_client();assert signin(other,'limited','limited-test-password').status_code==200
    assert other.get('/api/devices').status_code==200
    assert other.get('/api/audit').status_code==200
    assert other.get('/api/credentials').status_code==403
    assert post(other,'/api/devices',{'name':'x','ip_address':'10.0.0.2','platform':'cisco'}).status_code==403
    assert post(other,'/api/settings',{'workspace':'Forged'},'PATCH').status_code==403
    if role=='VIEWER':assert post(other,'/api/automation/runs',{'kind':'backup','device_id':1}).status_code==403


def test_disabled_user_and_role_changes_revoke_existing_session(admin,app):
    response=post(admin,'/api/users',{'username':'operator','role':'OPERATOR','password':'operator-test-password'})
    ident=response.json['data']['id']
    with app.app_context():db.session.get(User,ident).must_change_password=False;db.session.commit()
    other=app.test_client();signin(other,'operator','operator-test-password')
    assert other.get('/api/devices').status_code==200
    assert post(admin,'/api/users/'+str(ident),{'disabled':True},'PATCH').status_code==200
    assert other.get('/api/devices').status_code==401
    assert post(admin,'/api/users/1',{'role':'VIEWER'},'PATCH').status_code==422


def test_persistent_login_rate_limit(client):
    for _ in range(10):assert signin(client,password='wrong-password').status_code==401
    assert signin(client,password='wrong-password').status_code==429


def test_playbook_upload_versions_are_preserved(admin,app):
    def upload(replace=False):return admin.post('/api/playbooks',data={'file':(io.BytesIO(b'- hosts: all\n  gather_facts: false\n  tasks: []\n'),'custom.yml'),'vendor':'Any','replace':str(replace).lower()},headers={'X-CSRFToken':token(admin)})
    first=upload();assert first.status_code==201
    assert upload().status_code==409
    second=upload(True);assert second.status_code==201
    with app.app_context():
        rows=Playbook.query.filter_by(name='Custom/custom.yml').order_by(Playbook.id).all()
        assert len(rows)==2 and not rows[0].active and rows[1].version==2
        assert all(Path(row.path).is_file() for row in rows)


def test_managed_operations_validate_before_queue(admin):
    profile=credential(admin);cisco=device(admin,profile);mikrotik=device(admin,profile,'10.0.0.2','mikrotik')
    body={'kind':'vlan','device_id':mikrotik['id'],'parameters':{'vlan_id':42,'vlan_name':'test'}}
    assert post(admin,'/api/automation/runs',body).status_code==422
    body['device_id']=cisco['id'];body['parameters']['save_config']='false'
    assert post(admin,'/api/automation/runs',body).status_code==422
    body['parameters']['save_config']=False
    response=post(admin,'/api/automation/runs',body);assert response.status_code==202,response.json
    run=admin.get(response.json['data']['poll_url']).json['data'];assert run['status']=='PENDING'
    assert 'encrypted_parameters' not in run
    assert post(admin,response.json['data']['poll_url']+'/cancel',{}).json['data']['cancel_requested'] is True


def test_unknown_health_does_not_generate_data(admin,app,monkeypatch):
    row=device(admin)
    monkeypatch.setattr('app.services.telemetry.ping',lambda ip:True)
    response=admin.get('/api/devices/'+str(row['id'])+'/health')
    assert response.status_code==200
    data=response.json['data']
    assert data['cpu_percent'] is None and data['memory_percent'] is None
    assert data['connection_status']=='CREDENTIAL_REQUIRED'
    assert data['status']!='online'


def test_discovery_requires_verified_identity(admin,app):
    profile=credential(admin)
    response=post(admin,'/api/discovery/scans',{'cidr':'10.0.0.0/30','credential_id':profile['id']})
    assert response.status_code==202
    from app.models import DiscoveryScan,DiscoveryCandidate
    with app.app_context():
        row=DiscoveryCandidate(scan_id=response.json['data']['id'],ip_address='10.0.0.2',vendor_hint='cisco');db.session.add(row);db.session.commit();ident=row.id
    assert post(admin,'/api/discovery/candidates/import',{'candidate_ids':[ident]}).status_code==422
    assert admin.get('/api/devices').json['data']['items']==[]
    assert post(admin,'/api/discovery/scans',{'cidr':'10.0.0.1/24'}).status_code==422
    assert post(admin,'/api/discovery/scans',{'cidr':'10.0.0.0/16'}).status_code==422


def test_encryption_key_mismatch_fails_closed(app,config):
    from cryptography.fernet import Fernet
    from app.security import APIError
    with pytest.raises(APIError):
        create_app({**config,'ENCRYPTION_KEY':Fernet.generate_key().decode()})


def test_legacy_forms_still_accept_original_field_names(admin):
    response=admin.post('/add-device',data={'device_name':'Legacy input','ip_address':'10.0.0.7','username':'legacy','password':'legacy-test-password','os_type':'cisco_ios','csrf_token':token(admin)})
    assert response.status_code==302
    row=admin.get('/api/devices').json['data']['items'][0]
    assert row['name']=='Legacy input'
