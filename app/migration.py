"""Additive schema migration and reversible encryption of legacy secret values."""
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from cryptography.fernet import Fernet
from sqlalchemy import inspect, text
from flask import current_app
from .models import db, User, Device, Credential, AuditLog, Setting
from .security import encrypt, decrypt

ADDITIONS = {
 'user': {'password_hash':"TEXT NOT NULL DEFAULT ''",'name':"VARCHAR(100) DEFAULT ''",'role':"VARCHAR(20) NOT NULL DEFAULT 'ADMIN'",'must_change_password':'BOOLEAN NOT NULL DEFAULT 1','disabled_at':'VARCHAR(40)','session_version':'INTEGER NOT NULL DEFAULT 1','created_at':'VARCHAR(40)'},
 'group': {'created_at':'VARCHAR(40)','archived_at':'VARCHAR(40)'},
 'device': {'credential_id':'INTEGER REFERENCES credential(id)','snmp_credential_id':'INTEGER REFERENCES credential(id)','platform':"VARCHAR(20) DEFAULT 'cisco'",'ssh_port':'INTEGER DEFAULT 22','snmp_port':'INTEGER DEFAULT 161','model':"VARCHAR(100) DEFAULT ''",'notes':"TEXT DEFAULT ''",'health_json':'JSON','archived_at':'VARCHAR(40)','created_at':'VARCHAR(40)'},
 'audit_log': {'encrypted_output':'TEXT'}
}

def encrypted_snapshot(source, destination, match_source=False):
    """Write and verify an authenticated SQLite snapshot without plaintext temp files."""
    cipher=Fernet(current_app.config['ENCRYPTION_KEY'].encode())
    if destination.exists() and not match_source:
        if not cipher.decrypt(destination.read_bytes()).startswith(b'SQLite format 3\x00'):
            raise RuntimeError('Existing migration snapshot is invalid.')
        return
    with closing(sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True)) as src, closing(sqlite3.connect(':memory:')) as dst:
        src.backup(dst)
        raw=dst.serialize()
    if destination.exists():
        if cipher.decrypt(destination.read_bytes())!=raw:raise RuntimeError('An existing encrypted snapshot differs from its plaintext source; preserve both and resolve offline.')
        return
    token=cipher.encrypt(raw)
    if cipher.decrypt(token)!=raw:raise RuntimeError('Snapshot verification failed.')
    temporary=destination.with_name(destination.name+'.partial')
    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'wb') as stream:
        stream.write(token);stream.flush();os.fsync(stream.fileno())
    os.replace(temporary,destination)

def migrate():
    inspector=inspect(db.engine)
    tables=set(inspector.get_table_names())
    # Validate the key before copying, altering or encrypting any existing value.
    if 'setting' in tables:
        with db.engine.connect() as conn:
            row=conn.execute(text("SELECT value FROM setting WHERE key='internal_encryption_check'")).first()
            if row and decrypt(json.loads(row[0]))!='narsika-key-check':raise RuntimeError('Invalid encryption key.')
    if 'credential' in tables:
        with db.engine.connect() as conn:
            for row in conn.execute(text('SELECT encrypted_secret FROM credential')):decrypt(row[0])
    if 'device' in tables:
        columns={c['name'] for c in inspector.get_columns('device')}
        active=' WHERE archived_at IS NULL' if 'archived_at' in columns else ''
        with db.engine.connect() as conn:
            if conn.execute(text('SELECT ip_address FROM device'+active+' GROUP BY ip_address HAVING COUNT(*)>1')).first():
                raise RuntimeError('Existing inventory has duplicate active IP addresses. Resolve duplicates in an offline copy before migration.')
    changes=[]
    for table,columns in ADDITIONS.items():
        if table in tables:
            existing={c['name'] for c in inspector.get_columns(table)}
            changes.extend((table,key,definition) for key,definition in columns.items() if key not in existing)
    version=0
    if 'schema_version' in tables:
        with db.engine.connect() as conn:version=conn.execute(text('SELECT COALESCE(MAX(version),0) FROM schema_version')).scalar()
    if version>2:raise RuntimeError('Database schema is newer than this release. Restore the matching application; no downgrade was attempted.')
    source=Path(db.engine.url.database)
    upgrading=bool(tables) and (bool(changes) or version<2)
    if upgrading:encrypted_snapshot(source,source.with_name(source.name+'.before-v2.sqlite3.enc'))
    if changes:
        with db.engine.begin() as conn:
            for table,column,definition in changes:
                conn.execute(text(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {definition}'))
    db.create_all()
    for user in User.query.all():
        if not user.password_hash:
            user.password_hash=user.password or ''
            user.must_change_password=True
        if user.password_hash:user.password=''
    for device in Device.query.all():
        if device.credential_id is None:
            device.platform='mikrotik' if device.os_type=='mikrotik_routeros' else 'cisco'
            if device.username and device.password:
                profile=Credential(name=f'Imported device {device.id}',username=device.username,kind='ssh',
                    encrypted_secret=encrypt({'password':device.password,'enable_password':'','private_key':'','passphrase':''}))
                db.session.add(profile);db.session.flush();device.credential_id=profile.id
        if device.password or device.username:
            # Preserve all original fields, including incomplete/unassigned credentials.
            key=f'internal_legacy_device_{device.id}'
            if not db.session.get(Setting,key):
                token=encrypt({'username':device.username,'password':device.password})
                if decrypt(token)!={'username':device.username,'password':device.password}:raise RuntimeError('Legacy credential verification failed.')
                db.session.add(Setting(key=key,value=token))
            if device.credential_id:decrypt(db.session.get(Credential,device.credential_id).encrypted_secret)
            device.password='';device.username=''
    for record in AuditLog.query.filter(AuditLog.output.is_not(None),AuditLog.output!='').all():
        record.encrypted_output=encrypt(record.output)
        if decrypt(record.encrypted_output)!=record.output:raise RuntimeError('Historical audit encryption failed.')
        record.output=''
    db.session.commit()
    with db.engine.begin() as conn:
        conn.execute(text('CREATE UNIQUE INDEX IF NOT EXISTS uq_active_device_ip ON device(ip_address) WHERE archived_at IS NULL'))
        conn.execute(text('CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY)'))
    # Convert the exact snapshot name written by the previous Narsika release.
    old=source.with_name(source.name+'.before-v1.sqlite3')
    if old.is_file():
        encrypted_snapshot(old,old.with_name(old.name+'.enc'),match_source=True)
        old.unlink()
    db.session.remove()
    with db.engine.connect().execution_options(isolation_level='AUTOCOMMIT') as conn:
        conn.exec_driver_sql('PRAGMA journal_mode=WAL')
        if upgrading:
            conn.exec_driver_sql('VACUUM')
            checkpoint=conn.exec_driver_sql('PRAGMA wal_checkpoint(TRUNCATE)').one()
            if checkpoint[0]:raise RuntimeError('Stop other database users and retry migration to clear the legacy WAL.')
        conn.exec_driver_sql('INSERT OR IGNORE INTO schema_version(version) VALUES (2)')
    if source.is_file():source.chmod(0o600)
