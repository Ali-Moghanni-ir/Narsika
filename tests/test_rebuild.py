"""Release regression tests. Host UFW/systemd are never modified by this module."""
import copy
from pathlib import Path
import subprocess
from types import SimpleNamespace
import pytest
from tools import dependencies, install_native as native
from app.models import db, User, Device, OperationRun, FirewallReview
from app.security import encrypt, decrypt
from app.services import firewall as fw, jobs
from conftest import credential, device, post
from test_firewall import fake_device, snapshot, intent, observed_after


@pytest.mark.parametrize('url', ['http://mirror.invalid/simple', 'https://u:p@mirror.invalid/simple',
                               'https://mirror.invalid/?secret=x', 'https://mirror.invalid/#x',
                               'https://mirror.invalid:bad/simple', 'https://mirror.invalid/\nx', ''])
def test_package_source_requires_explicit_https(url):
    with pytest.raises(ValueError):
        dependencies.index_url(url)


def test_package_options_and_no_inherited_insecure_pip_settings(monkeypatch):
    options=dependencies.settings({'NARSIKA_PIP_INDEX_URL':'https://mirror.invalid/simple',
                                  'PIP_INDEX_URL':'https://ignored.invalid/simple',
                                  'PIP_DEFAULT_TIMEOUT':'300','PIP_RETRIES':'15'})
    args=dependencies.command('/venv/python',Path('/source/requirements.txt'),options,True)
    assert args[args.index('--index-url')+1]=='https://mirror.invalid/simple/'
    assert args[args.index('--timeout')+1]=='300'
    assert args[args.index('--retries')+1]=='15'
    assert '--dry-run' in args and '--ignore-installed' in args
    monkeypatch.setenv('PIP_TRUSTED_HOST','mirror.invalid')
    monkeypatch.setenv('PIP_EXTRA_INDEX_URL','https://unselected.invalid/simple')
    env=dependencies.environment()
    assert 'PIP_TRUSTED_HOST' not in env and 'PIP_EXTRA_INDEX_URL' not in env
    assert 'PIP_CONFIG_FILE' in env


@pytest.mark.parametrize('key,value', [('PIP_RETRIES','-1'),('PIP_RETRIES','21'),
                                    ('PIP_DEFAULT_TIMEOUT','0'),('PIP_DEFAULT_TIMEOUT','bad')])
def test_download_limits_are_validated(key,value):
    with pytest.raises(ValueError):dependencies.settings({key:value})


def test_dependency_failure_can_retry_same_release_without_loosening_pins(monkeypatch):
    calls=[]
    def run(args,**kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=1 if len(calls)==1 else 0)
    monkeypatch.setattr(dependencies.subprocess,'run',run)
    monkeypatch.setattr('builtins.input',lambda _: 'https://second.invalid/simple')
    selected=dependencies.install('/venv/python',Path('/release/requirements.txt'),dependencies.settings({}))
    assert selected['index']=='https://second.invalid/simple/'
    assert calls[0][-1]==calls[1][-1]=='/release/requirements.txt'
    assert calls[-1]==['/venv/python','-m','pip','check']


@pytest.mark.parametrize('active,existing', [(False,False),(False,True),(True,False),(True,True)])
def test_ufw_plan_does_not_depend_on_status_or_numbering(active,existing):
    commands=native.firewall_commands(['10.1.0.0/16','192.168.4.0/24'],8000,has_rules=existing)
    # Model per-family prepend semantics, not the real UFW backend.
    rules=['existing'] if existing else []
    for command in commands:
        assert command[:2]==['ufw','prepend']
        assert not set(command)&{'insert','delete','reset','disable','default','on'}
        rules.insert(0,command[2]+' '+command[command.index('from')+1])
    assert rules[:3]==['allow 10.1.0.0/16','allow 192.168.4.0/24','deny any']
    if existing:assert rules[-1]=='existing'


@pytest.fixture
def deployment(tmp_path,monkeypatch):
    base=tmp_path/'opt';base.mkdir()
    config=tmp_path/'etc/narsika/narsika.env'
    data=tmp_path/'data'
    unit=tmp_path/'unit/narsika.service';unit.parent.mkdir()
    admin=tmp_path/'bin/narsika-admin';admin.parent.mkdir()
    for key,value in {'BASE':base,'CONFIG':config,'DATA':data,'UNIT':unit,'ADMIN_COMMAND':admin}.items():
        monkeypatch.setattr(native,key,value)
    monkeypatch.setattr(native.os,'chown',lambda *a:None)
    monkeypatch.setattr(native,'validate_service_account',lambda a:None)
    monkeypatch.setattr(native.pwd,'getpwnam',lambda n:SimpleNamespace(pw_uid=10001,pw_gid=10001))
    monkeypatch.setattr(native.shutil,'disk_usage',lambda p:SimpleNamespace(free=10*1024**3))
    monkeypatch.setattr(native,'release_files',lambda root:[native.ROOT/'deploy/narsika.service'])
    monkeypatch.setattr(native.dependencies,'install',lambda *a,**k:dependencies.settings({}))
    monkeypatch.setattr(native,'health',lambda port:None)
    monkeypatch.setattr(native.socket,'socket',lambda:SimpleNamespaceSocket())
    calls=[]
    state={'active':False,'enabled':False,'fail':None}
    def run(args,**kwargs):
        args=[str(a) for a in args];calls.append(args)
        if 'is-active' in args:return SimpleNamespace(returncode=0 if state['active'] else 3)
        if 'is-enabled' in args:return SimpleNamespace(returncode=0 if state['enabled'] else 1)
        if any(x.endswith('/configure.py') for x in args) and '--check' not in args:
            config.write_text('NARSIKA_SECRET_KEY=preserved-session-key\nNARSIKA_ENCRYPTION_KEY=preserved-encryption-key\n')
        if any(x.endswith('/bootstrap.py') for x in args):
            (data/'narsika.db').write_bytes(b'preserved database and account')
        failed=(state['fail']=='ufw' and args[:3]==['ufw','prepend','deny']
                or state['fail']=='activation' and args[:3]==['systemctl','enable','--now'])
        if failed:raise subprocess.CalledProcessError(1,args)
        output='Status: inactive\n' if args[:2]==['ufw','status'] else '127.0.0.1' if args[:2]==['hostname','-I'] else ''
        return SimpleNamespace(returncode=0,stdout=output)
    monkeypatch.setattr(native.subprocess,'run',run)
    answers=iter(['8000','10.1.0.0/16','ENABLE','n'])
    monkeypatch.setattr('builtins.input',lambda _:next(answers))
    return SimpleNamespace(base=base,config=config,data=data,unit=unit,admin=admin,calls=calls,state=state)


class SimpleNamespaceSocket:
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def bind(self,address):pass


def test_first_install_firewall_failure_does_not_activate_service(deployment):
    d=deployment;d.state['fail']='ufw'
    with pytest.raises(subprocess.CalledProcessError):native.install()
    assert not any(c[:3]==['systemctl','enable','--now'] for c in d.calls)
    assert not (d.base/'current').exists() and not d.unit.exists()
    assert (d.data/'narsika.db').read_bytes()==b'preserved database and account'
    assert 'preserved-encryption-key' in d.config.read_text()
    record=next((d.base/'releases').glob('*/deployment-status.json')).read_text()
    assert 'host firewall' in record


def test_first_install_activation_failure_preserves_recoverable_files(deployment):
    d=deployment;d.state['fail']='activation'
    with pytest.raises(subprocess.CalledProcessError):native.install()
    assert not (d.base/'current').is_symlink() and not d.unit.exists() and not d.admin.exists()
    recovered=next((d.base/'releases').glob('*/failed-deployment'))
    assert (recovered/'narsika.service').is_file() and (recovered/'narsika-admin').is_file()
    assert d.config.exists() and (d.data/'narsika.db').exists()


def test_upgrade_activation_failure_restores_old_release_and_configuration(deployment):
    d=deployment;d.state.update(active=True,enabled=True,fail='activation')
    old=d.base/'releases/old';old.mkdir(parents=True)
    (d.base/'current').symlink_to(old)
    d.config.parent.mkdir(parents=True);d.config.write_text('NARSIKA_PORT=8000\nNARSIKA_ENCRYPTION_KEY=original-key\n')
    d.unit.write_text('previous unit');d.admin.write_text('previous admin tool')
    with pytest.raises(subprocess.CalledProcessError):native.install()
    assert (d.base/'current').resolve()==old
    assert d.config.read_text()=='NARSIKA_PORT=8000\nNARSIKA_ENCRYPTION_KEY=original-key\n'
    assert d.unit.read_text()=='previous unit' and d.admin.read_text()=='previous admin tool'
    assert ['systemctl','start','narsika'] in d.calls


def test_missing_keys_with_existing_data_stops_before_bootstrap(deployment):
    d=deployment;d.data.mkdir();(d.data/'narsika.db').write_bytes(b'original')
    with pytest.raises(SystemExit,match='original encryption key'):native.install()
    assert not d.calls and not d.config.exists()


def test_service_is_activated_only_after_firewall_on_success(deployment):
    d=deployment;native.install()
    activation=d.calls.index(['systemctl','enable','--now','narsika'])
    firewall=d.calls.index(['ufw','--force','enable'])
    assert firewall<activation and (d.base/'current').is_symlink()
    assert 'healthy' in next((d.base/'releases').glob('*/deployment-status.json')).read_text()


@pytest.mark.parametrize('port',[443,50000])
def test_return_path_block_is_classified_as_possible_lockout(port):
    data=intent(chain='output')
    data['changes'][0].update(source='10.0.0.1',destination='10.0.0.100',port=port,action='block',service='custom')
    assert fw.compile_plan(data,fake_device(),snapshot(),'ADMIN')['risk']=='LOCKOUT'


def test_input_chain_return_tuple_does_not_create_false_lockout():
    data=intent(chain='input')
    data['changes'][0].update(source='10.0.0.1',destination='10.0.0.100',port=50000,action='block',service='custom')
    assert fw.compile_plan(data,fake_device(),snapshot(),'ADMIN')['risk']=='HIGH'


@pytest.mark.parametrize('change',['password','role','credential'])
def test_queued_run_rechecks_account_and_credential_revision(admin,app,monkeypatch,change):
    profile=credential(admin);target=device(admin,profile)
    response=post(admin,'/api/automation/runs',{'kind':'backup','device_id':target['id']})
    ident=response.json['data']['run_id']
    if change=='credential':
        assert post(admin,f'/api/credentials/{profile["id"]}',{'password':'changed-network-secret'},'PATCH').status_code==200
    else:
        with app.app_context():
            user=db.session.get(User,1)
            if change=='password':user.set_password('a-new-password-for-tests')
            else:user.role='OPERATOR';user.session_version+=1
            db.session.commit()
    from app.services import backups
    monkeypatch.setattr(backups,'capture',lambda *a:pytest.fail('Unauthorized queued run reached network execution'))
    jobs.JobManager.execute(SimpleNamespace(app=app),ident)
    with app.app_context():
        row=db.session.get(OperationRun,ident)
        assert row.status=='FAILED'
        assert row.error_code==('TARGET_CHANGED' if change=='credential' else 'AUTHORIZATION_CHANGED')


def test_cisco_traceability_matches_receipt_not_an_unwritten_tag(admin,app):
    target=device(admin,platform='cisco')
    with app.app_context():
        obj=db.session.get(Device,target['id'])
        plan=fw.compile_plan(intent(acl='TEST',sequence=10),obj,snapshot('cisco'),'ADMIN')
        row=FirewallReview(id='receipt-match',device_id=obj.id,actor_id=1,encrypted_plan=encrypt(plan),
                           checksum=fw.digest(plan),expires_at=0,status='SUCCESS')
        db.session.add(row);db.session.commit()
        after=observed_after(plan)
        after['rules']=[dict(acl='TEST',**entry) for entry in after['acls']['TEST']]
        before=after['fingerprint']
        result=fw.annotate_receipt_matches(after,obj)
        assert result['rules'][-1]['receipt_id']=='receipt-match'
        assert result['fingerprint']==before
        assert all('NARSIKA_FW_' not in cmd for cmd in plan['items'][0]['commands'])
        result['rules'][-1]['body']='deny ip any any'
        result['rules'][-1].pop('receipt_id')
        assert 'receipt_id' not in fw.annotate_receipt_matches(result,obj)['rules'][-1]


def test_doctor_is_non_mutating_and_does_not_disclose_keys(tmp_path,monkeypatch):
    from tools import doctor_native as doctor
    base=tmp_path/'opt';release=base/'releases/test';release.mkdir(parents=True)
    (base/'current').symlink_to(release)
    (release/'.venv/bin').mkdir(parents=True)
    (release/'.venv/bin/python').write_text('unused test executable')
    data=tmp_path/'data';data.mkdir();(data/'narsika.db').write_bytes(b'original')
    config=tmp_path/'narsika.env'
    config.write_text('NARSIKA_SECRET_KEY=never-show-session-key\nNARSIKA_ENCRYPTION_KEY=never-show-encryption-key\n')
    config.chmod(0o640)
    before=config.read_bytes();calls=[]
    def run(args,**kwargs):
        calls.append(args);return SimpleNamespace(returncode=0,stdout='never-show-session-key')
    monkeypatch.setattr(doctor.subprocess,'run',run)
    def unavailable(*a,**k):raise OSError('test unavailable')
    monkeypatch.setattr(doctor.urllib.request,'build_opener',unavailable)
    result=doctor.inspect(base,data,config)
    assert 'never-show' not in str(result)
    assert next(r for r in result if r['name']=='Configuration and keys')['status']=='PASS'
    assert next(r for r in result if r['name']=='Loopback HTTP health')['status']=='FAIL'
    assert config.read_bytes()==before and (data/'narsika.db').read_bytes()==b'original'
    assert not any(set(args)&{'install','start','stop','restart','reset','enable','disable','delete'} for args in calls)


def test_vlan_frontend_ignores_old_target_responses():
    script=r'''
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const elements={'#vlan-target':{value:'1'},'#vlan-list':{},'#vlan-form':{}};
const requests=[];
global.document={body:{dataset:{page:'vlan'}}};
global.window={addEventListener(){},NarsikaExecution:{},Narsika:{
  $:id=>elements[id],D:{},h:x=>String(x),options:()=>'',
  empty:(a,b)=>a+' '+b,apiFetch:url=>new Promise((resolve,reject)=>requests.push({url,resolve,reject}))
}};
vm.runInThisContext(fs.readFileSync('app/static/js/automation.js','utf8'));
(async()=>{
  elements['#vlan-target'].value='2';const second=elements['#vlan-target'].onchange();
  requests[1].resolve({data:{items:[{id:20,name:'correct-target',status:'active',interfaces:[]}]}});await second;
  requests[0].resolve({data:{items:[{id:10,name:'old-target',status:'active',interfaces:[]}]}});await Promise.resolve();
  assert.match(elements['#vlan-list'].innerHTML,/correct-target/);
  assert.doesNotMatch(elements['#vlan-list'].innerHTML,/old-target/);
  const stale=elements['#vlan-target'].onchange();elements['#vlan-target'].value='';await elements['#vlan-target'].onchange();
  requests[2].reject(new Error('stale-error'));await stale;
  assert.match(elements['#vlan-list'].innerHTML,/No Cisco devices/);
  assert.doesNotMatch(elements['#vlan-list'].innerHTML,/stale-error/);
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
    subprocess.run(['node','-e',script],cwd=native.ROOT,check=True,capture_output=True,text=True)
