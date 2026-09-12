import json
import os
import re
import selectors
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from flask import current_app
import yaml
from ..models import db,Playbook,RunArtifact
from ..security import decrypt,fail,integer,text,address
from . import catalog
from .network import secret,known_path,device_lock
from .backups import capture,store_backup

ROOT=Path(__file__).resolve().parents[2]

def validate_operation(kind,params,device):
    if 'save_config' in params and not isinstance(params['save_config'],bool):fail('save_config must be true or false.')
    if kind=='vlan':
        if device.platform!='cisco':fail('VLAN management requires Cisco IOS. Use a RouterOS playbook for MikroTik.')
        ident=integer(params.get('vlan_id'),'VLAN ID',2,4094)
        if ident in (1002,1003,1004,1005):fail('Reserved VLAN IDs cannot be changed.')
        action=params.get('operation','create')
        if action not in ('create','remove'):fail('Invalid VLAN operation.')
        name=text(params.get('vlan_name',''),'VLAN name',32,required=action!='remove')
        if action!='remove' and not re.fullmatch(r'[A-Za-z0-9_-]+',name):fail('Use an ASCII VLAN name without spaces.')
        return dict(vlan_id=ident,vlan_name=name,operation=action,save_config=bool(params.get('save_config',False)))
    if kind=='acl':
        name=text(params.get('name'),'rule name',32)
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]*',name):fail('Invalid access-list name.')
        protocol=params.get('protocol');action=params.get('action');chain=params.get('chain','forward')
        if protocol not in ('tcp','udp','icmp','ip') or action not in ('permit','deny') or chain not in ('input','forward','output'):fail('Invalid rule protocol, action or chain.')
        values={k:('any' if params.get(k)=='any' else address(params.get(k),False)) for k in ('source','destination')}
        port=integer(params.get('port'),'destination port') if protocol in ('tcp','udp') else None
        return dict(name=name,protocol=protocol,action=action,chain=chain,port=port,**values,save_config=bool(params.get('save_config',False)))
    return params

def configuration_play(kind,p,device):
    if kind=='vlan':
        args={'lines':[f'no vlan {p["vlan_id"]}']} if p['operation']=='remove' else {'parents':[f'vlan {p["vlan_id"]}'],'lines':['name '+p['vlan_name']]}
        task={'name':'Configure reviewed VLAN','cisco.ios.ios_config':{**args,'save_when':'changed' if p['save_config'] else 'never'}}
    elif device.platform=='cisco':
        addr=lambda value:'any' if value=='any' else 'host '+value
        line=f'{p["action"]} {p["protocol"]} {addr(p["source"])} {addr(p["destination"])}'
        if p['port'] is not None:line+=f' eq {p["port"]}'
        task={'name':'Configure reviewed access rule','cisco.ios.ios_config':{'parents':['ip access-list extended '+p['name']],'lines':[line],'save_when':'changed' if p['save_config'] else 'never'}}
    else:
        fields={'chain':p['chain'],'action':'accept' if p['action']=='permit' else 'drop','comment':'narsika:'+p['name']}
        if p['protocol']!='ip':fields['protocol']=p['protocol']
        if p['source']!='any':fields['src-address']=p['source']
        if p['destination']!='any':fields['dst-address']=p['destination']
        if p['port'] is not None:fields['dst-port']=str(p['port'])
        line='/ip firewall filter add '+' '.join(k+'="'+str(v)+'"' for k,v in fields.items())
        tag='narsika:'+p['name']
        # Omitted selectors must also match; otherwise a restricted existing rule
        # could incorrectly be reported equivalent to a reviewed "any" rule.
        expected={'protocol':'','src-address':'','dst-address':'','dst-port':'',**fields,'disabled':'false'}
        checks=' || '.join(f'([:tostr [/ip firewall filter get $ids {key}]] != "{value}")' for key,value in expected.items())
        cmd='{ :local ids [/ip firewall filter find where comment="'+tag+'"]; :if ([:len $ids] > 1) do={ :error "Ambiguous managed rule" }; :if ([:len $ids] = 0) do={ '+line+'; :put "NARSIKA_CHANGED" } else={ :if ('+checks+') do={ :error "Existing rule conflicts; review manually" }; :put "NARSIKA_UNCHANGED" } }'
        task={'name':'Ensure reviewed RouterOS access rule','community.routeros.command':{'commands':[cmd]},'register':'result','changed_when':"'NARSIKA_CHANGED' in result.stdout[0]",'failed_when':"not (result.stdout[0] is search('NARSIKA_(CHANGED|UNCHANGED)'))"}
    return [{'name':'Narsika reviewed operation','hosts':'all','gather_facts':False,'tasks':[task]}]

def safe_environment(tmp,config,control_dir):
    env={k:v for k,v in os.environ.items() if k in ('PATH','LANG','LC_ALL','VIRTUAL_ENV','PYTHONPATH','SSL_CERT_FILE','ANSIBLE_COLLECTIONS_PATH')}
    env.update(ANSIBLE_CONFIG=str(config),ANSIBLE_LOCAL_TEMP=str(tmp/'local'),ANSIBLE_REMOTE_TEMP='/tmp/.ansible-narsika',
        ANSIBLE_PERSISTENT_CONTROL_PATH_DIR=str(control_dir),ANSIBLE_STDOUT_CALLBACK='narsika_safe',
        ANSIBLE_CALLBACK_PLUGINS=str(ROOT/'callback_plugins'),ANSIBLE_LIBSSH_LOOK_FOR_KEYS='False',ANSIBLE_NOCOLOR='1',ANSIBLE_DISPLAY_ARGS_TO_STDOUT='false',
        NARSIKA_ANSIBLE_KNOWN_HOSTS=str(known_path()),ANSIBLE_HOST_KEY_CHECKING='True',ANSIBLE_LIBSSH_HOST_KEY_AUTO_ADD='False',ANSIBLE_LIBSSH_HOST_KEY_CHECKING='True')
    return env

def run_ansible(run,params,device):
    executable=shutil.which('ansible-playbook')
    if not executable or os.name!='posix':fail('Ansible requires the Linux/Docker runtime.','CAPABILITY_UNAVAILABLE',503)
    values=secret(device.credential)
    with device_lock(device.id):
        address(device.ip_address)
        # Capture independently of custom playbook behavior for managed configuration workflows.
        if run.kind in ('vlan','acl'):
            capture(device,run.id);db.session.commit()
        with tempfile.TemporaryDirectory(prefix='job-',dir=Path(current_app.config['DATA_DIR'])/'runs') as directory, tempfile.TemporaryDirectory(prefix='nsk-') as control_directory:
            tmp=Path(directory)
            if run.kind=='playbook':
                book=db.session.get(Playbook,params['playbook_id'])
                if not book:fail('Playbook is unavailable.','NOT_FOUND',404)
                playpath=catalog.source(book)
                variables=params.get('variables',{})
                if book.name in ('Original/cisco_acl.yml','Original/mikrotik_acl.yml','Original/manage-vlan.yml'):
                    capture(device,run.id);db.session.commit()
            else:
                playpath=tmp/'operation.yml';playpath.write_text(yaml.safe_dump(configuration_play(run.kind,params,device)))
                variables={}
            inventory={'all':{'children':{k:{'hosts':{'target':{}}} for k in ('cisco','mikrotik','cisco_routers')},'hosts':{'target':{'ansible_host':device.ip_address}}}}
            # All groups refer to the same selected host, preserving original playbook host patterns.
            protected=dict(ansible_host=device.ip_address,ansible_port=device.ssh_port,ansible_user=device.credential.username,
                ansible_password=values.get('password',''),ansible_become=device.platform=='cisco' and bool(values.get('enable_password')),ansible_become_method='enable',
                ansible_become_password=values.get('enable_password',''),ansible_connection='ansible.netcommon.network_cli',
                ansible_network_os='cisco.ios.ios' if device.platform=='cisco' else 'community.routeros.routeros',
                ansible_network_cli_ssh_type='libssh',ansible_libssh_look_for_keys=False,ansible_host_key_checking=True,
                ansible_libssh_host_key_checking=True,ansible_libssh_host_key_auto_add=False,ansible_command_timeout=45,
                narsika_targets='all',narsika_artifact_root=str(tmp/'artifacts'))
            if values.get('private_key'):
                key=tmp/'identity';key.write_text(values['private_key']);key.chmod(0o600)
                protected.update(ansible_private_key_file=str(key),ansible_private_key_passphrase=values.get('passphrase',''))
            inventory['all']['hosts']['target'].update({k:v for k,v in protected.items() if k.startswith('ansible_')})
            variables={**variables,'narsika_targets':'all','narsika_artifact_root':str(tmp/'artifacts')}
            inv=tmp/'inventory.json';inv.write_text(json.dumps(inventory));inv.chmod(0o600)
            varfile=tmp/'variables.json';varfile.write_text(json.dumps(variables));varfile.chmod(0o600)
            config=tmp/'ansible.cfg';config.write_text('[defaults]\nhost_key_checking = True\nretry_files_enabled = False\n[libssh_connection]\nhost_key_auto_add = False\n')
            process=subprocess.Popen([executable,'-i',str(inv),str(playpath),'--extra-vars','@'+str(varfile)],cwd=str(tmp),
                env=safe_environment(tmp,config,Path(control_directory)),stdout=subprocess.PIPE,stderr=subprocess.STDOUT,start_new_session=True)
            start=time.monotonic();selector=selectors.DefaultSelector();selector.register(process.stdout,selectors.EVENT_READ)
            os.set_blocking(process.stdout.fileno(),False);buffer=b'';status=None;events=0
            try:
                while process.poll() is None:
                    db.session.refresh(run)
                    if run.cancel_requested or time.monotonic()-start>current_app.config['JOB_TIMEOUT']:
                        status='CANCELLED' if run.cancel_requested else 'TIMEOUT';os.killpg(process.pid,signal.SIGTERM)
                        try:process.wait(timeout=3)
                        except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);process.wait()
                        break
                    for key,_ in selector.select(timeout=.2):
                        chunk=os.read(key.fd,8192)
                        if not chunk:continue
                        buffer+=chunk
                        while b'\n' in buffer:
                            line,buffer=buffer.split(b'\n',1)
                            message=decode_event(line)
                            if message:
                                events+=1;run.output=((run.output or '')+message+'\n')[-100000:];db.session.commit()
                        if len(buffer)>65536:buffer=b''
                # Drain complete callback messages after process exit.
                remainder=buffer+(process.stdout.read() or b'')
                for line in remainder.splitlines():
                    message=decode_event(line)
                    if message:events+=1;run.output=((run.output or '')+message+'\n')[-100000:]
                db.session.commit()
                # Preserve before-change backups even if a later task fails or is cancelled.
                collect_artifacts(tmp,run,device)
                if status=='TIMEOUT':fail('Ansible execution timed out; check the device for partial changes.','TIMEOUT',504)
                if status=='CANCELLED':return 'Execution cancelled. Commands already sent are not rolled back.'
                if process.returncode:fail('Ansible failed. Review task status, SSH trust, credentials and YAML syntax.','ANSIBLE_FAILED',502)
                if not events or not any(line.startswith(('OK [','FAILED [','UNREACHABLE [')) for line in (run.output or '').splitlines()):fail('Ansible returned no verifiable task events.','NO_TASK_EVENTS',502)
                return 'Ansible completed. Review changed/skipped task events and saved artifacts.'
            finally:
                selector.close()
                try:os.killpg(process.pid,signal.SIGTERM)
                except ProcessLookupError:pass
                process.stdout.close()
                if process.poll() is None:process.wait(timeout=5)

def decode_event(raw):
    if not raw.startswith(b'NARSIKA_EVENT '):return None
    try:item=json.loads(raw[len(b'NARSIKA_EVENT '):])
    except (ValueError,UnicodeDecodeError):return None
    if not isinstance(item,dict):return None
    event=item.get('event');action=item.get('action','')
    if event not in ('ok','failed','unreachable','skipped','recap') or not isinstance(action,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{0,120}',action):return None
    return f'{event.upper()} [{action}]'+(' changed' if item.get('changed') is True else '')

def collect_artifacts(tmp,run,device):
    from cryptography.fernet import Fernet
    import hashlib,uuid
    root=tmp/'artifacts'
    if not root.exists():return
    from itertools import islice
    paths=list(islice(root.rglob('*'),1001))
    rejected=len(paths)>1000
    total=0
    for path in paths[:1000]:
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            rejected=True;continue
        if not path.is_file():continue
        size=path.stat().st_size
        if size>5*1024*1024 or total+size>50*1024*1024:
            rejected=True;continue
        total+=size
        raw=path.read_bytes();name=uuid.uuid4().hex+'.artifact'
        dest=Path(current_app.config['BACKUP_DIR'])/name
        dest.write_bytes(Fernet(current_app.config['ENCRYPTION_KEY'].encode()).encrypt(raw));dest.chmod(0o600)
        db.session.add(RunArtifact(run_id=run.id,name=path.name[:180],path=name,size_bytes=len(raw),checksum=hashlib.sha256(raw).hexdigest()))
        if path.suffix in ('.cfg','.rsc'):
            try:store_backup(device,run.id,raw.decode())
            except UnicodeDecodeError:pass
    db.session.commit()
    if rejected:
        fail('Artifact collection was incomplete (unsafe path, over 1000 entries, 5 MiB/file or 50 MiB total). Accepted files were saved. Inspect the playbook before rerunning.', 'ARTIFACT_LIMIT', 502)
