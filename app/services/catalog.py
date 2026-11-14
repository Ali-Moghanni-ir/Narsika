from pathlib import Path
import re
import uuid
import yaml
from flask import current_app
from ..models import db, Playbook
from ..security import fail
from . import platforms

ROOT=Path(__file__).resolve().parents[2]

# Form fields for the packaged playbooks, so operators fill a form instead of writing JSON.
# Patterns follow each playbook's own asserts; the Original playbooks have none, so these are their only guard.
IPV4=r'(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(\.(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}'
def F(name: str, label: str, kind: str = 'text', required: bool = True, pattern: str | None = None,
      choices: list | None = None, low: int | None = None, high: int | None = None, example: object = None) -> dict:
    """One form field description, sent to the browser and used for server-side checks."""
    return {'name':name,'label':label,'type':kind,'required':required,'pattern':pattern,'choices':choices,'min':low,'max':high,'example':example}
SAVE=F('save_config','Save to startup configuration','bool',False)
VLAN=lambda low=2:F('vlan_id','VLAN ID','number',low=low,high=4094,example=40)
ACL=[F('acl_name','Rule / ACL name',pattern=r'[A-Za-z0-9_-]{1,64}',example='BLOCK-WEB'),
     F('action','Action','choice',choices=['permit','deny']),
     F('protocol','Protocol','choice',choices=['ip','tcp','udp','icmp']),
     F('src_ip','Source address',pattern='any|'+IPV4,example='any'),
     F('dst_ip','Destination address',pattern='any|'+IPV4,example='192.0.2.10'),
     F('port','Destination port (TCP/UDP only)','number',False,low=1,high=65535,example=443)]
FORMS={
 'Cisco/03_ensure_vlan.yml':[VLAN(),F('vlan_name','VLAN name',pattern=r'[A-Za-z0-9_-]{1,32}',example='Engineering'),SAVE],
 'Cisco/04_configure_access_port.yml':[F('interface_name','Interface',example='GigabitEthernet1/0/10'),F('access_vlan','Access VLAN','number',low=2,high=4094,example=40),
     F('interface_description','Description',example='Engineering workstation'),F('allow_trunk_conversion','Allow converting a trunk port','bool',False),SAVE],
 'Cisco/05_ntp_syslog.yml':[F('ntp_server','NTP server',pattern=IPV4,example='192.0.2.20'),F('syslog_server','Syslog server',pattern=IPV4,example='192.0.2.21'),SAVE],
 'Linux/02_package_updates.yml':[F('refresh_package_index','Refresh the package index first','bool',False)],
 'Linux/03_ensure_package.yml':[F('package_name','Package',example='htop'),F('package_state','State','choice',choices=['present','absent','latest'])],
 'Linux/04_manage_service.yml':[F('service_name','Service',example='cron'),F('service_state','State','choice',choices=['started','stopped','restarted','reloaded']),
     F('service_enabled','Start at boot','tristate',False),F('allow_management_service_change','Allow changing SSH/network services','bool',False)],
 'MikroTik/03_ensure_vlan_interface.yml':[F('vlan_name','VLAN interface name',example='vlan40'),F('parent_interface','Parent interface',example='bridge-lan'),VLAN(1)],
 'MikroTik/04_dhcp_reservation.yml':[F('dhcp_server','DHCP server',example='dhcp-lan'),F('lease_address','IP address',pattern=IPV4,example='192.0.2.50'),
     F('lease_mac','MAC address',pattern=r'([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}',example='02:00:00:00:00:50'),F('lease_comment','Comment',example='Office printer')],
 'MikroTik/05_static_route.yml':[F('route_name','Route name',pattern=r'[A-Za-z0-9_-]{1,48}',example='branch-network'),F('route_destination','Destination (CIDR)',example='198.51.100.0/24'),
     F('route_gateway','Gateway',pattern=IPV4,example='192.0.2.1'),F('route_distance','Distance','number',low=1,high=254,example=1),F('allow_default_route','Allow a default route (0.0.0.0/0)','bool',False)],
 'Original/cisco_acl.yml':ACL,'Original/mikrotik_acl.yml':ACL,
 'Original/manage-vlan.yml':[VLAN(),F('vlan_name','VLAN name',pattern=r'[A-Za-z0-9_-]{1,32}',example='Engineering'),F('state','State','choice',choices=['present','absent'])],
}

def form_variables(book: object, variables: object) -> dict:
    """Validate form input for a packaged playbook; uploaded playbooks keep their free variables."""
    fields = FORMS.get(book.name) if book.builtin else None
    if fields is None:
        return variables
    if not isinstance(variables, dict):
        fail('Variables must be an object.')
    result = {}
    if isinstance(variables.get('narsika_apply'), bool):
        result['narsika_apply'] = variables['narsika_apply']
    for field in fields:
        name = field['name']
        value = variables.get(name)
        if field['type'] == 'bool':
            result[name] = value is True
            continue
        if field['type'] == 'tristate':
            if value not in (None, True, False):
                fail(field['label'] + ': choose yes, no or unchanged.')
            result[name] = value
            continue
        if value is None or value == '':
            if field['required']:
                fail(field['label'] + ' is required.')
            continue
        if field['type'] == 'number':
            result[name] = form_number(field, value)
        else:
            result[name] = form_text(field, value)
    return result


def form_number(field: dict, value: object) -> int:
    # ASCII digits only: str.isdigit() also accepts characters such as '²' that int() rejects.
    text = str(value).strip()
    if isinstance(value, bool) or not re.fullmatch(r'[0-9]{1,6}', text) or not field['min'] <= int(text) <= field['max']:
        fail(f"{field['label']}: enter a number from {field['min']} to {field['max']}.")
    return int(text)


def form_text(field: dict, value: object) -> str:
    text = str(value).strip()
    if not text or '\n' in text or '\r' in text or len(text) > 128:
        fail(field['label'] + ': enter one line of up to 128 characters.')
    if field['choices'] and text not in field['choices']:
        fail(field['label'] + ': choose ' + ', '.join(field['choices']) + '.')
    if field['pattern'] and not re.fullmatch(field['pattern'], text):
        fail(field['label'] + ' has an invalid format.')
    return text


def seed_catalog():
    # Only actual packaged playbooks are registered; no demonstration records are seeded.
    for vendor in platforms.vendors():
        for file in sorted((ROOT/'Playbooks'/vendor).glob('*.yml')):
            name=vendor+'/'+file.name
            existing=Playbook.query.filter_by(name=name,builtin=True).first()
            example=ROOT/'Playbooks/examples'/vendor/file.name.replace('.yml','.vars.yml')
            defaults=yaml.safe_load(example.read_text()) if example.exists() else {}
            if existing:
                existing.path=str(file)
                existing.defaults=defaults or {}
                continue
            example=ROOT/'Playbooks/examples'/vendor/file.name.replace('.yml','.vars.yml')
            defaults=yaml.safe_load(example.read_text()) if example.exists() else {}
            db.session.add(Playbook(name=name,title=file.stem[3:].replace('_',' ').title(),vendor=vendor,
                path=str(file),defaults=defaults or {},builtin=True))
    for file in sorted((ROOT/'Playbooks/Original').glob('*.yml')):
        name='Original/'+file.name
        existing=Playbook.query.filter_by(name=name,builtin=True).first()
        if existing:existing.path=str(file)
        else:
            vendor='MikroTik' if 'mikrotik' in file.name else 'Cisco'
            db.session.add(Playbook(name=name,title='Original · '+file.stem.replace('_',' '),vendor=vendor,path=str(file),defaults={},builtin=True))
    db.session.commit()

def source(playbook):
    path=Path(playbook.path).resolve()
    roots=[ROOT/'Playbooks',ROOT/'Playbooks/Original',Path(current_app.config['DATA_DIR'])/'playbooks']
    if not any(path.is_relative_to(root.resolve()) for root in roots) or not path.is_file():
        fail('Playbook source is unavailable.','NOT_FOUND',404)
    return path

def upload(file,vendor,replace=False):
    from werkzeug.utils import secure_filename
    name=secure_filename(file.filename or '')
    if not name or not name.lower().endswith(('.yml','.yaml')):fail('Upload a .yml or .yaml file.')
    if vendor not in ('Any',)+platforms.vendors():fail('Invalid vendor.')
    raw=file.read(2*1024*1024+1)
    if not raw or len(raw)>2*1024*1024:fail('Upload a non-empty file up to 2 MB.')
    try:raw.decode('utf-8')
    except UnicodeDecodeError:fail('Use a UTF-8 YAML file.')
    try:
        document = yaml.compose(raw.decode('utf-8'))
    except yaml.YAMLError as error:
        mark = getattr(error, 'problem_mark', None)
        fail('Invalid YAML' + (f' at line {mark.line + 1}.' if mark else '.'))
    if not isinstance(document, yaml.SequenceNode) or not document.value:
        fail('A playbook must be a non-empty YAML list of plays.')
    if any(not isinstance(play, yaml.MappingNode) for play in document.value):
        fail('Each play must be a YAML mapping.')
    # Admin playbooks are trusted code. This is a format check, not a content sandbox.
    old=Playbook.query.filter_by(name='Custom/'+name,active=True).first()
    if old and not replace:fail('Confirm replacement; the previous version will be retained.','CONFLICT',409)
    path=Path(current_app.config['DATA_DIR'])/'playbooks'/(uuid.uuid4().hex+'.yml')
    path.write_bytes(raw);path.chmod(0o600)
    if old:old.active=False
    item=Playbook(name='Custom/'+name,title=name,vendor=vendor,path=str(path),version=old.version+1 if old else 1,defaults={},builtin=False)
    db.session.add(item)
    return item
