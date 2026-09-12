import hashlib
import uuid
from pathlib import Path
from flask import current_app
from cryptography.fernet import Fernet
from ..models import db, Backup
from ..security import fail
from .network import export_configuration

def store_backup(device,run_id,content):
    raw=content.encode()
    key=Fernet(current_app.config['ENCRYPTION_KEY'].encode())
    name=uuid.uuid4().hex+'.enc'
    path=Path(current_app.config['BACKUP_DIR'])/name
    path.write_bytes(key.encrypt(raw));path.chmod(0o600)
    backup=Backup(device_id=device.id,operation_run_id=run_id,path=name,checksum=hashlib.sha256(raw).hexdigest(),size_bytes=len(raw))
    db.session.add(backup);db.session.flush()
    return backup

def content(backup):
    root=Path(current_app.config['BACKUP_DIR']).resolve();path=(root/backup.path).resolve()
    if not path.is_relative_to(root) or not path.is_file():fail('Backup file is missing.','NOT_FOUND',404)
    try:raw=Fernet(current_app.config['ENCRYPTION_KEY'].encode()).decrypt(path.read_bytes())
    except Exception:fail('Unable to decrypt backup. Restore the correct encryption key.','KEY_ERROR',503)
    if hashlib.sha256(raw).hexdigest()!=backup.checksum:fail('Backup checksum mismatch.','INTEGRITY_ERROR',500)
    return raw.decode()

def capture(device,run_id):
    value=export_configuration(device)
    if not value.strip():fail('The device returned an empty configuration.','EMPTY_BACKUP',502)
    return store_backup(device,run_id,value)
