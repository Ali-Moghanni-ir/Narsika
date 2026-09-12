from pathlib import Path
import uuid
import yaml
from flask import current_app
from ..models import db, Playbook
from ..security import fail

ROOT=Path(__file__).resolve().parents[2]

def seed_catalog():
    # Only actual packaged playbooks are registered; no demonstration records are seeded.
    for vendor in ('Cisco','MikroTik'):
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
    if vendor not in ('Any','Cisco','MikroTik'):fail('Invalid vendor.')
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
