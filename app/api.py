"""Authenticated, CSRF-protected API. No demonstration records are created."""
import hashlib
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from flask import Blueprint, current_app, jsonify, request, Response
from flask_login import current_user
from cryptography.fernet import Fernet
from werkzeug.utils import secure_filename
from .models import db, User, Group, Device, Credential, Setting, AuditEvent, AuditLog, OperationRun, Playbook, Backup, DiscoveryScan, DiscoveryCandidate, RunArtifact, now
from .security import require, payload, integer, text, address, network, password_policy, encrypt, decrypt, audit, fail
from .services import catalog, network as net, telemetry, backups
from .services.jobs import enqueue
from .services.automation import validate_operation

api=Blueprint('api',__name__)

def ok(data=None,status=200):
    return jsonify(data=data if data is not None else {},meta={}),status

def get(model,ident,active=True):
    row=db.session.get(model,integer(ident,'record ID',1,2**63-1))
    if row is None or (active and getattr(row,'archived_at',None)):
        fail('Record not found.','NOT_FOUND',404)
    return row

def boolean(value,name):
    if not isinstance(value,bool):fail(name+' must be true or false.')
    return value

def settings_data():
    result={'workspace':'Narsika','interval':10,'compact':False}
    result.update({r.key:r.value for r in Setting.query.filter(Setting.key.in_(result.keys())).all()})
    return result

def bootstrap():
    if not current_user.is_authenticated:return {'user':None,'devices':[],'groups':[],'credentials':[],'users':[],'audit':[],'settings':{}}
    return dict(user=current_user.public(),devices=[d.public() for d in Device.query.filter_by(archived_at=None).order_by(Device.name)],
        groups=[g.public() for g in Group.query.filter_by(archived_at=None).order_by(Group.name)],
        credentials=[c.public() for c in Credential.query.order_by(Credential.name)] if current_user.role in ('ADMIN','OPERATOR') else [],
        users=[u.public() for u in User.query.order_by(User.id)] if current_user.role=='ADMIN' else [current_user.public()],
        audit=[a.public() for a in AuditEvent.query.order_by(AuditEvent.id.desc()).limit(500)],settings=settings_data())

@api.get('/bootstrap')
@require()
def bootstrap_info():return ok(bootstrap())

@api.get('/session')
def session_info():return ok({'user':current_user.public() if current_user.is_authenticated else None})

def reference(value,model,kind=None):
    if value in (None,''):return None
    row=get(model,value)
    if kind and row.kind!=kind:fail('Credential type does not match this field.')
    return row.id

DEVICE_FIELDS={'name','ip_address','platform','ssh_port','snmp_port','group_id','credential_id','snmp_credential_id','model','notes'}
def normalize_device(data,row=None):
    values={}
    for key in ('name','model','notes'):
        if key in data:values[key]=text(data[key],key,2000 if key=='notes' else 100,key=='name')
    if 'ip_address' in data:
        values['ip_address']=address(data['ip_address'])
        duplicate=Device.query.filter_by(ip_address=values['ip_address'],archived_at=None).first()
        if duplicate and (row is None or duplicate.id!=row.id):fail('This IP address is already in inventory.','CONFLICT',409)
    if 'platform' in data:
        if data['platform'] not in ('cisco','mikrotik'):fail('Select Cisco IOS or MikroTik RouterOS.')
        values['platform']=data['platform'];values['os_type']='cisco_ios' if data['platform']=='cisco' else 'mikrotik_routeros'
    for key in ('ssh_port','snmp_port'):
        if key in data:values[key]=integer(data[key],key)
    for key,model,kind in (('group_id',Group,None),('credential_id',Credential,'ssh'),('snmp_credential_id',Credential,'snmpv3')):
        if key in data:values[key]=reference(data[key],model,kind)
    if row and any(key in values and values[key]!=getattr(row,key) for key in ('ip_address','platform','ssh_port','credential_id','snmp_port','snmp_credential_id')):values['health_json']=None
    return values

@api.route('/devices',methods=['GET','POST'])
@require()
def devices():
    if request.method=='GET':return ok({'items':[d.public() for d in Device.query.filter_by(archived_at=None).order_by(Device.name)]})
    return create_device(payload(DEVICE_FIELDS,('name','ip_address','platform')))

@require('admin')
def create_device(data):
    row=Device(**normalize_device(data));db.session.add(row);db.session.flush()
    audit('Device added',row.ip_address);db.session.commit();return ok(row.public(),201)

@api.route('/devices/<int:ident>',methods=['GET','PATCH','DELETE'])
@require()
def device_item(ident):
    row=get(Device,ident)
    if request.method=='GET':return ok(row.public())
    return change_device(row)

@require('admin')
def change_device(row):
    if request.method=='DELETE':
        row.archived_at=now();audit('Device archived',row.ip_address)
    else:
        for key,value in normalize_device(payload(DEVICE_FIELDS),row).items():setattr(row,key,value)
        audit('Device updated',row.ip_address)
    telemetry.COUNTERS.pop(row.id,None)
    db.session.commit();return ok(row.public())

@api.post('/devices/<int:ident>/restore')
@require('admin')
def restore_device(ident):
    row=get(Device,ident,False)
    duplicate=Device.query.filter_by(ip_address=row.ip_address,archived_at=None).first()
    if duplicate and duplicate.id!=row.id:fail('An active device already uses this IP address.','CONFLICT',409)
    row.archived_at=None;audit('Device restored',row.ip_address);db.session.commit();return ok(row.public())

@api.route('/groups',methods=['GET','POST'])
@require()
def groups():
    if request.method=='GET':return ok({'items':[r.public() for r in Group.query.filter_by(archived_at=None).order_by(Group.name)]})
    return create_group(payload({'name'},('name',)))

@require('admin')
def create_group(data):
    row=Group(name=text(data['name'],'group name'));db.session.add(row)
    audit('Group added',row.name);db.session.commit();return ok(row.public(),201)

@api.route('/groups/<int:ident>',methods=['PATCH','DELETE'])
@require('admin')
def group_item(ident):
    row=get(Group,ident)
    if request.method=='DELETE':
        if Device.query.filter_by(group_id=row.id,archived_at=None).first():fail('Move active devices out of this group first.','CONFLICT',409)
        row.archived_at=now();action='Group archived'
    else:row.name=text(payload({'name'},('name',))['name'],'group name');action='Group renamed'
    audit(action,row.name);db.session.commit();return ok(row.public())

CREDENTIAL_FIELDS={'name','username','kind','password','enable_password','private_key','passphrase','auth_password','priv_password'}
def credential_values(data,row=None):
    kind=data.get('kind',row.kind if row else 'ssh')
    if kind not in ('ssh','snmpv3'):fail('Unsupported credential type.')
    if row and kind!=row.kind:fail('Create a separate profile to change credential type.')
    values={'name':text(data.get('name',row.name if row else None),'credential name'),
        'username':text(data.get('username',row.username if row else None),'username'), 'kind':kind}
    secret=decrypt(row.encrypted_secret) if row else {}
    keys=('password','enable_password','private_key','passphrase') if kind=='ssh' else ('auth_password','priv_password')
    for key in keys:
        if key in data:
            if not isinstance(data[key],str) or len(data[key])>32768:fail('Invalid credential value.')
            secret[key]=data[key]
    if kind=='ssh' and not(secret.get('password') or secret.get('private_key')):fail('Provide an SSH password or private key.')
    if kind=='snmpv3' and any(len(secret.get(k,''))<8 for k in keys):fail('SNMPv3 authentication and privacy passwords require at least 8 characters.')
    values['encrypted_secret']=encrypt(secret);return values

@api.route('/credentials',methods=['GET','POST'])
@require('admin')
def credentials():
    if request.method=='GET':return ok({'items':[c.public() for c in Credential.query.order_by(Credential.name)]})
    row=Credential(**credential_values(payload(CREDENTIAL_FIELDS,('name','username','kind'))));db.session.add(row)
    audit('Credential added',row.name);db.session.commit();return ok(row.public(),201)

@api.patch('/credentials/<int:ident>')
@require('admin')
def credential_item(ident):
    row=get(Credential,ident)
    for key,value in credential_values(payload(CREDENTIAL_FIELDS),row).items():setattr(row,key,value)
    Device.query.filter((Device.credential_id==row.id)|(Device.snmp_credential_id==row.id)).update({'health_json':None},synchronize_session=False)
    with telemetry.SAMPLE_LOCK:telemetry.COUNTERS.clear()
    audit('Credential updated',row.name);db.session.commit();return ok(row.public())

@api.route('/users',methods=['GET','POST'])
@require('admin')
def users():
    if request.method=='GET':return ok({'items':[u.public() for u in User.query.order_by(User.id)]})
    data=payload({'username','name','role','password'},('username','role'))
    username=text(data['username'],'username',64)
    if not re.fullmatch(r'[A-Za-z0-9_.-]+',username):fail('Use letters, digits, dots, dashes or underscores for usernames.')
    if data['role'] not in ('VIEWER','OPERATOR','ADMIN'):fail('Invalid role.')
    row=User(username=username,name=text(data.get('name',username),'name'),role=data['role'])
    import secrets
    generated = secrets.token_urlsafe(24) if 'password' not in data else None
    row.set_password(password_policy(generated or data['password']));db.session.add(row)
    audit('User added',username);db.session.commit()
    result = row.public()
    if generated:result['temporary_password'] = generated
    return ok(result,201)

@api.patch('/users/<int:ident>')
@require('admin')
def user_item(ident):
    row=get(User,ident);data=payload({'name','role','password','disabled','reset_password'})
    reset = boolean(data['reset_password'],'reset_password') if 'reset_password' in data else False
    if reset and 'password' in data:fail('Choose one password reset method.')
    generated = None
    if reset:
        import secrets
        generated = secrets.token_urlsafe(24)
        data['password'] = generated
    role=data.get('role',row.role)
    if role not in ('VIEWER','OPERATOR','ADMIN'):fail('Invalid role.')
    disabled=boolean(data['disabled'],'disabled') if 'disabled' in data else bool(row.disabled_at)
    if row.id==current_user.id and (disabled or role!='ADMIN'):fail('Another administrator must change your own access.')
    if row.role=='ADMIN' and row.is_active and (disabled or role!='ADMIN') and User.query.filter_by(role='ADMIN',disabled_at=None).count()<=1:fail('The last active administrator must remain enabled.')
    if 'name' in data:row.name=text(data['name'],'name')
    access_changed=row.role!=role or bool(row.disabled_at)!=disabled
    row.role=role;row.disabled_at=now() if disabled else None
    if 'password' in data:
        row.set_password(password_policy(data['password']));row.must_change_password=True;access_changed=False
    if access_changed:row.session_version+=1
    audit('User updated',row.username);db.session.commit()
    result = row.public()
    if generated:result['temporary_password'] = generated
    return ok(result)

@api.route('/settings',methods=['GET','PATCH'])
@require()
def settings():
    if request.method=='GET':return ok(settings_data())
    return update_settings()

@require('admin')
def update_settings():
    data=payload({'workspace','interval','compact'})
    if 'workspace' in data:data['workspace']=text(data['workspace'],'workspace name',80)
    if 'interval' in data:data['interval']=integer(data['interval'],'refresh interval',5,60)
    if 'compact' in data:data['compact']=boolean(data['compact'],'compact')
    for key,value in data.items():
        row=db.session.get(Setting,key)
        if row:row.value=value
        else:db.session.add(Setting(key=key,value=value))
    audit('Settings updated','Workspace');db.session.commit();return ok(settings_data())

@api.get('/system')
@require()
def system_info():return ok({'database':'SQLite','worker_available':'jobs' in current_app.extensions,'ansible_available':bool(shutil.which('ansible-playbook')),'allowed_networks':current_app.config['ALLOWED_NETWORKS'],'scan_max_hosts':current_app.config['SCAN_MAX_HOSTS'],'snmp':'v3 authPriv / SHA-256 / AES-128'})

@api.get('/devices/<int:ident>/health')
@require()
def device_health(ident):return ok(telemetry.health(get(Device,ident)))

@api.get('/devices/<int:ident>/interfaces')
@require()
def device_interfaces(ident):return ok(telemetry.interfaces(get(Device,ident)))

@api.get('/devices/<int:ident>/vlans')
@require()
def device_vlans(ident):
    device=get(Device,ident)
    if device.platform!='cisco':fail('VLAN inventory is available for Cisco IOS.','UNSUPPORTED',422)
    with net.device_lock(device.id),net.connection(device) as connection:
        result=connection.send_command('show vlan brief',use_textfsm=True,read_timeout=20)
    if not isinstance(result,list):fail('This IOS VLAN output is unsupported by the parser.','UNSUPPORTED_OUTPUT',502)
    return ok({'items':[{'id':r.get('vlan_id'),'name':r.get('vlan_name'),'status':r.get('status'),'interfaces':r.get('interfaces',[])} for r in result]})

@api.route('/devices/<int:ident>/host-key',methods=['GET','POST'])
@require('admin')
def device_host_key(ident):
    row=get(Device,ident);return key_response(row.ip_address,row.ssh_port)

def key_response(ip,port):
    if request.method=='GET':return ok(net.probe_key(ip,port)[0])
    data=payload({'fingerprint'},('fingerprint',));result=net.trust_key(ip,port,text(data['fingerprint'],'fingerprint',100))
    audit('SSH host key trusted',ip,detail=data['fingerprint']);db.session.commit();return ok(result)

@api.route('/playbooks',methods=['GET','POST'])
@require()
def playbooks():
    if request.method=='GET':return ok({'items':[r.public() for r in Playbook.query.filter_by(active=True).order_by(Playbook.vendor,Playbook.title)]})
    return upload_playbook()

@require('admin')
def upload_playbook():
    file=request.files.get('file') or request.files.get('playbook')
    if not file:fail('Choose a YAML file.')
    vendor=request.form.get('vendor','Any')
    if vendor not in ('Cisco','MikroTik','Any'):fail('Invalid playbook vendor.')
    row=catalog.upload(file,vendor,request.form.get('replace')=='true')
    candidate=Path(row.path)
    try:
        audit('Playbook uploaded',row.name,detail=f'Version {row.version}');db.session.commit()
    except Exception:
        db.session.rollback()
        candidate.unlink(missing_ok=True)
        raise
    return ok(row.public(),201)

@api.get('/playbooks/<int:ident>/source')
@require('operate')
def playbook_source(ident):
    row=get(Playbook,ident);return Response(catalog.source(row).read_bytes(),mimetype='text/yaml',headers={'Content-Disposition':'attachment; filename="'+secure_filename(row.name)+'"'})

def queue_run(kind,device,parameters):
    if 'jobs' not in current_app.extensions and not current_app.testing:fail('The operation worker is not running.','CAPABILITY_UNAVAILABLE',503)
    run=enqueue(kind,device.id if device else None,current_user.id,parameters)
    audit('Run queued',str(device.ip_address if device else run.id),detail=kind);db.session.commit()
    return run

def accepted(run):return ok({'run_id':run.id,'status':run.status,'poll_url':f'/api/automation/runs/{run.id}'},202)

@api.route('/automation/runs',methods=['GET','POST'])
@require()
def runs():
    if request.method=='GET':return ok({'items':[r.public() for r in OperationRun.query.order_by(OperationRun.id.desc()).limit(100)]})
    return create_run(payload({'kind','device_id','playbook_id','variables','parameters'},('kind','device_id')))

@require('operate')
def create_run(data):
    device=get(Device,data['device_id']);kind=data['kind']
    if kind not in ('playbook','vlan','acl','backup'):fail('Unsupported operation.')
    net.secret(device.credential);address(device.ip_address)
    if kind=='playbook':
        book=get(Playbook,data.get('playbook_id'))
        if not book.active:fail('Select the active version of the playbook.','CONFLICT',409)
        if book.vendor!='Any' and book.vendor.lower()!=device.platform:fail('The selected playbook does not support this platform.')
        variables=data.get('variables',{})
        if not isinstance(variables,dict):fail('Playbook variables must be a JSON object.')
        if any(k.startswith('ansible_') or k in ('narsika_targets','narsika_artifact_root') for k in variables):fail('Connection and artifact variables are managed by the server.')
        if book.name in ('Original/cisco_acl.yml','Original/mikrotik_acl.yml'):
            cleaned=validate_operation('acl',{'name':variables.get('acl_name'),'protocol':variables.get('protocol'),'action':variables.get('action'),'source':variables.get('src_ip'),'destination':variables.get('dst_ip'),'port':variables.get('port')},device)
            variables={**variables,'acl_name':cleaned['name'],'protocol':cleaned['protocol'],'action':cleaned['action'],'src_ip':cleaned['source'],'dst_ip':cleaned['destination'],'port':cleaned['port']}
        if book.name=='Original/manage-vlan.yml':
            action={'present':'create','absent':'remove'}.get(variables.get('state'))
            cleaned=validate_operation('vlan',{'vlan_id':variables.get('vlan_id'),'vlan_name':variables.get('vlan_name',''),'operation':action},device)
            variables={**variables,'vlan_id':cleaned['vlan_id'],'vlan_name':cleaned['vlan_name']}
        params={'playbook_id':book.id,'variables':variables}
    else:
        params=data.get('parameters',{})
        if not isinstance(params,dict):fail('Parameters must be a JSON object.')
        allowed={'vlan':{'vlan_id','vlan_name','operation','save_config'},'acl':{'name','protocol','action','chain','source','destination','port','save_config'},'backup':set()}[kind]
        if set(params)-allowed:fail('Unknown operation parameters.')
        params=validate_operation(kind,params,device)
    return accepted(queue_run(kind,device,params))

@api.get('/automation/runs/<int:ident>')
@require()
def run_item(ident):
    row=get(OperationRun,ident);result=row.public();result['artifacts']=[r.public() for r in RunArtifact.query.filter_by(run_id=row.id)]
    return ok(result)

@api.post('/automation/runs/<int:ident>/cancel')
@require('operate')
def cancel_run(ident):
    row=get(OperationRun,ident)
    if row.requested_by_id!=current_user.id and current_user.role!='ADMIN':fail('Only the run owner or an administrator can cancel it.','FORBIDDEN',403)
    if row.status in ('PENDING','RUNNING'):
        row.cancel_requested=True;audit('Run cancellation requested',row.id);db.session.commit()
    return ok(row.public())

@api.get('/artifacts/<int:ident>/download')
@require('operate')
def artifact_download(ident):
    row=get(RunArtifact,ident);root=Path(current_app.config['BACKUP_DIR']).resolve();path=(root/row.path).resolve()
    if not path.is_relative_to(root) or not path.is_file():fail('Artifact file is unavailable.','NOT_FOUND',404)
    try:raw=Fernet(current_app.config['ENCRYPTION_KEY'].encode()).decrypt(path.read_bytes())
    except Exception:fail('Unable to decrypt this artifact.','KEY_ERROR',503)
    if hashlib.sha256(raw).hexdigest()!=row.checksum:fail('Artifact integrity check failed.','INTEGRITY_ERROR',500)
    audit('Artifact downloaded',row.id);db.session.commit()
    return Response(raw,mimetype='application/octet-stream',headers={'Content-Disposition':'attachment; filename="'+(secure_filename(row.name) or 'artifact')+'"'})

@api.route('/backups',methods=['GET','POST'])
@require()
def backup_list():
    if request.method=='GET':return ok({'items':[r.public() for r in Backup.query.filter_by(archived_at=None).order_by(Backup.id.desc())]})
    data=payload({'device_id'},('device_id',));return create_run({'kind':'backup',**data})

@api.route('/backups/<int:ident>',methods=['GET','DELETE'])
@require('operate')
def backup_item(ident):
    row=get(Backup,ident)
    if request.method=='DELETE':return archive_backup(row)
    return ok({**row.public(),'content':backups.content(row)})

@require('admin')
def archive_backup(row):
    row.archived_at=now();audit('Backup archived',row.id);db.session.commit();return ok(row.public())

@api.get('/backups/<int:ident>/download')
@require('operate')
def backup_download(ident):
    row=get(Backup,ident);raw=backups.content(row);audit('Backup downloaded',row.id);db.session.commit()
    return Response(raw,mimetype='text/plain',headers={'Content-Disposition':f'attachment; filename="device-{row.device_id}-backup-{row.id}.cfg"'})

@api.get('/audit')
@require()
def audit_list():
    rows=[r.public() for r in AuditEvent.query.order_by(AuditEvent.id.desc()).limit(1000)]
    for old in AuditLog.query.order_by(AuditLog.id.desc()).limit(1000):
        rows.append({'id':'legacy-'+str(old.id),'actor':old.admin.username if old.admin else 'unknown','action':old.action_type,'target':old.target_ip,'result':(old.status or 'unknown').lower(),'detail':'Historical event','created_at':old.timestamp.isoformat()+'Z' if old.timestamp else ''})
    rows.sort(key=lambda row:row['created_at'],reverse=True)
    return ok({'items':rows[:1000]})

@api.route('/discovery/scans',methods=['GET','POST'])
@require('operate')
def discovery_scans():
    if request.method=='GET':return ok({'items':[scan_data(r) for r in DiscoveryScan.query.order_by(DiscoveryScan.id.desc()).limit(50)]})
    data=payload({'cidr','ssh_port','credential_id'},('cidr',));cidr=str(network(data['cidr']))
    port=integer(data.get('ssh_port',22),'SSH port');credential=reference(data.get('credential_id'),Credential,'ssh')
    if 'jobs' not in current_app.extensions and not current_app.testing:fail('The operation worker is not running.','CAPABILITY_UNAVAILABLE',503)
    run=enqueue('discovery',None,current_user.id,{'cidr':cidr,'ssh_port':port})
    audit('Discovery scan queued',cidr)
    row=DiscoveryScan(run_id=run.id,cidr=cidr,ssh_port=port,credential_id=credential);db.session.add(row);db.session.commit()
    return ok({'id':row.id,'run_id':run.id,'status':run.status},202)

def scan_data(row):return {'id':row.id,'cidr':row.cidr,'ssh_port':row.ssh_port,'credential_id':row.credential_id,'run':get(OperationRun,row.run_id).public()}

@api.get('/discovery/scans/<int:ident>')
@require('operate')
def discovery_scan_item(ident):
    row=get(DiscoveryScan,ident);return ok({**scan_data(row),'candidates':[r.public() for r in DiscoveryCandidate.query.filter_by(scan_id=row.id)]})

@api.get('/discovery/scans/<int:ident>/candidates')
@require('operate')
def discovery_candidates(ident):
    get(DiscoveryScan,ident);return ok({'items':[r.public() for r in DiscoveryCandidate.query.filter_by(scan_id=ident)]})

@api.route('/discovery/candidates/<int:ident>/host-key',methods=['GET','POST'])
@require('admin')
def discovery_key(ident):
    row=get(DiscoveryCandidate,ident);scan=get(DiscoveryScan,row.scan_id);return key_response(row.ip_address,scan.ssh_port)

@api.post('/discovery/candidates/<int:ident>/verify')
@require('operate')
def verify_candidate(ident):
    row=get(DiscoveryCandidate,ident);scan=get(DiscoveryScan,row.scan_id)
    data=payload({'platform','credential_id'},('platform','credential_id'))
    if data['platform'] not in ('cisco','mikrotik'):fail('Select the expected platform.')
    credential=get(Credential,data['credential_id']);net.secret(credential)
    row.verified_at=None;row.verified_platform=None;row.verified_credential_id=None;db.session.commit()
    target=SimpleNamespace(id='candidate-'+str(row.id),ip_address=row.ip_address,ssh_port=scan.ssh_port,platform=data['platform'],credential=credential)
    with net.device_lock(target.id),net.connection(target,enable=False) as conn:
        output=net.command(conn,'show version' if target.platform=='cisco' else '/system resource print')
    verified=bool(re.search(r'Cisco.*(?:IOS|Software)|Cisco IOS|Catalyst',output,re.I)) if target.platform=='cisco' else 'version:' in output and ('board-name:' in output or 'architecture-name:' in output)
    if not verified:fail('The authenticated response did not verify the selected platform.','UNSUPPORTED_OUTPUT',502)
    row.verified_at=now();row.verified_platform=target.platform;row.verified_credential_id=credential.id
    audit('Discovery candidate verified',row.ip_address);db.session.commit();return ok(row.public())

@api.post('/discovery/candidates/import')
@require('admin')
def import_candidates():
    data=payload({'candidate_ids','group_id'},('candidate_ids',));ids=data['candidate_ids']
    if not isinstance(ids,list) or not 1<=len(ids)<=256:fail('Select between 1 and 256 candidates.')
    group=reference(data.get('group_id'),Group);created=[];skipped=[]
    for ident in dict.fromkeys(integer(i,'candidate ID',1,2**63-1) for i in ids):
        row=get(DiscoveryCandidate,ident);scan=get(DiscoveryScan,row.scan_id)
        if not row.verified_at or (datetime.now(timezone.utc)-datetime.fromisoformat(row.verified_at.replace('Z','+00:00'))).total_seconds()>1800:fail('Verify every selected candidate within the last 30 minutes before importing.')
        if Device.query.filter_by(ip_address=row.ip_address,archived_at=None).first():skipped.append(row.id);continue
        device=Device(name=row.ip_address,ip_address=address(row.ip_address),platform=row.verified_platform,
            os_type='cisco_ios' if row.verified_platform=='cisco' else 'mikrotik_routeros',ssh_port=scan.ssh_port,
            credential_id=row.verified_credential_id,group_id=group)
        db.session.add(device);db.session.flush();row.imported_device_id=device.id;created.append(device.public())
    audit('Discovery candidates imported',str(len(created)));db.session.commit();return ok({'items':created,'skipped':skipped},201)
