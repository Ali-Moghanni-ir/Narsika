from pathlib import Path
import pytest
from cryptography.fernet import Fernet
from app import create_app
from app.models import db, User

@pytest.fixture
def config(tmp_path):
    return {'TESTING':True,'START_WORKER':False,'SECRET_KEY':'test-session-key-'+('x'*40),'ENCRYPTION_KEY':Fernet.generate_key().decode(),
        'DATA_DIR':str(tmp_path),'BACKUP_DIR':str(tmp_path/'backups'),'SQLALCHEMY_DATABASE_URI':'sqlite:///'+str(tmp_path/'narsika.db'),
        'ADMIN_PASSWORD':'initial-password-for-tests','ADMIN_USERNAME':'admin','ALLOWED_NETWORKS':['127.0.0.0/8','10.0.0.0/8'],'JOB_TIMEOUT':15}

@pytest.fixture
def app(config):
    application=create_app(config)
    yield application
    manager=application.extensions.get('jobs')
    if manager:manager.close()
    with application.app_context():db.session.remove();db.engine.dispose()

@pytest.fixture
def client(app):return app.test_client()

def token(client):
    with client.session_transaction() as state:return state.get('csrf','')

def post(client,path,data,method='POST'):
    return client.open(path,method=method,json=data,headers={'X-CSRFToken':token(client)})

def signin(client,username='admin',password='initial-password-for-tests'):
    client.get('/login')
    return post(client,'/login',{'username':username,'password':password})

@pytest.fixture
def admin(client):
    assert signin(client).status_code==200
    assert post(client,'/change-password',{'current':'initial-password-for-tests','password':'changed-password-for-tests','confirm':'changed-password-for-tests'}).status_code==200
    return client

def credential(client,kind='ssh'):
    data={'name':'Test credential '+kind,'username':'test-network-user','kind':kind}
    data.update({'password':'network-test-secret'} if kind=='ssh' else {'auth_password':'auth-test-secret','priv_password':'priv-test-secret'})
    response=post(client,'/api/credentials',data)
    assert response.status_code==201,response.json
    return response.json['data']

def device(client,profile=None,ip='10.0.0.1',platform='cisco'):
    data={'name':'Integration fixture','ip_address':ip,'platform':platform}
    if profile:data['credential_id']=profile['id']
    response=post(client,'/api/devices',data)
    assert response.status_code==201,response.json
    return response.json['data']
