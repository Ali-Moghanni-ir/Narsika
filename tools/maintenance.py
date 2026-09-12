#!/usr/bin/env python3
"""Offline retention: preview by default; archive encrypted data before an explicit purge."""
import argparse
import base64
import fcntl
import gzip
import json
import os
from pathlib import Path
import sqlite3
import sys
from datetime import datetime,timedelta,timezone
from cryptography.fernet import Fernet
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from runtime import load_environment


def maintain(data_dir,backup_dir,key,days,apply=False):
    if days<1:raise ValueError('Retention must be at least one day.')
    source=data_dir/'narsika.db'
    if not source.is_file():raise ValueError('No existing Narsika database was found.')
    cutoff=(datetime.now(timezone.utc)-timedelta(days=days)).isoformat().replace('+00:00','Z')
    with (data_dir/'worker.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError('Stop Narsika before retention maintenance.') from None
        connection=sqlite3.connect(source);connection.row_factory=sqlite3.Row
        try:
            connection.execute('PRAGMA foreign_keys=ON');connection.execute('PRAGMA secure_delete=ON')
            connection.execute('BEGIN IMMEDIATE')
            marker=connection.execute("SELECT value FROM setting WHERE key='internal_encryption_check'").fetchone()
            cipher=Fernet(key.encode())
            if not marker or json.loads(cipher.decrypt(json.loads(marker[0]).encode()))!='narsika-key-check':raise ValueError('Invalid encryption key.')
            backup_rows=[dict(r) for r in connection.execute('SELECT * FROM backup WHERE archived_at IS NOT NULL AND archived_at<? ORDER BY id LIMIT 500',(cutoff,))]
            artifact_rows=[dict(r) for r in connection.execute("SELECT a.* FROM run_artifact a JOIN operation_run r ON r.id=a.run_id WHERE r.finished_at<? AND r.status NOT IN ('PENDING','RUNNING') ORDER BY a.id LIMIT 500",(cutoff,))]
            audit_rows=[dict(r) for r in connection.execute('SELECT * FROM audit_event WHERE created_at<? ORDER BY id LIMIT 500',(cutoff,))]
            log_rows=[dict(r) for r in connection.execute('SELECT * FROM audit_log WHERE timestamp<? ORDER BY id LIMIT 500',(cutoff[:19].replace('T',' '),))]
            run_rows=[dict(r) for r in connection.execute("SELECT r.* FROM operation_run r WHERE r.finished_at<? AND r.status NOT IN ('PENDING','RUNNING') AND NOT EXISTS(SELECT 1 FROM backup b WHERE b.operation_run_id=r.id) AND NOT EXISTS(SELECT 1 FROM run_artifact a WHERE a.run_id=r.id) AND NOT EXISTS(SELECT 1 FROM discovery_scan s WHERE s.run_id=r.id) ORDER BY r.id LIMIT 500",(cutoff,))]
            tables={'backup':backup_rows,'run_artifact':artifact_rows,'audit_event':audit_rows,'audit_log':log_rows,'operation_run':run_rows}
            summary={'cutoff':cutoff,'apply':apply,'records':{name:len(rows) for name,rows in tables.items()},'archive':None}
            if not apply or not any(tables.values()):connection.rollback();return summary
            files={}
            for row in backup_rows+artifact_rows:
                path=(backup_dir/row['path']).resolve()
                if not path.is_relative_to(backup_dir.resolve()):raise ValueError('Invalid stored artifact path.')
                if not path.is_file():raise ValueError('A retention candidate file is missing. Restore it before purging metadata.')
                files[row['path']]=base64.b64encode(path.read_bytes()).decode()
            raw=gzip.compress(json.dumps({'format':1,'created_at':datetime.now(timezone.utc).isoformat(),'tables':tables,'files':files}).encode())
            encoded=cipher.encrypt(raw)
            if cipher.decrypt(encoded)!=raw:raise ValueError('Archive verification failed.')
            directory=data_dir/'archives';directory.mkdir(mode=0o700,exist_ok=True)
            import uuid
            archive=directory/('retention-'+uuid.uuid4().hex+'.json.gz.enc')
            fd=os.open(archive,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'wb') as stream:stream.write(encoded);stream.flush();os.fsync(stream.fileno())
            for table,rows in tables.items():
                # Table names come only from the fixed mapping above.
                connection.executemany(f'DELETE FROM "{table}" WHERE id=?',[(r['id'],) for r in rows])
            connection.commit()
            # Metadata is committed first; a crash can only leave an extra encrypted file.
            for name in files:
                referenced=connection.execute('SELECT 1 FROM backup WHERE path=? UNION ALL SELECT 1 FROM run_artifact WHERE path=?',(name,name)).fetchone()
                if not referenced:(backup_dir/name).unlink(missing_ok=True)
            connection.execute('VACUUM');connection.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            summary['archive']=str(archive)
            return summary
        except Exception:connection.rollback();raise
        finally:connection.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--days',type=int,required=True)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args();load_environment()
    data_dir=Path(os.getenv('NARSIKA_DATA_DIR',str(ROOT/'instance'))).resolve()
    backup_dir=Path(os.getenv('NARSIKA_BACKUP_DIR',str(data_dir/'backups'))).resolve()
    print(json.dumps(maintain(data_dir,backup_dir,os.environ['NARSIKA_ENCRYPTION_KEY'],args.days,args.apply),indent=2))

if __name__=='__main__':main()
