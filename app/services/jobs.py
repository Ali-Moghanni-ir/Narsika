import concurrent.futures
import fcntl
import threading
import time
import shutil
import hashlib
from pathlib import Path
from ..models import db,OperationRun,Device,User,now
from ..security import encrypt,decrypt,audit,APIError,fail

def worker_available(app):
    manager=app.extensions.get('jobs')
    return bool(manager and manager.available())

class JobManager:
    def __init__(self,app):
        self.app=app
        self.lockfile=open(Path(app.config['DATA_DIR'])/'worker.lock','a')
        try:fcntl.flock(self.lockfile,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            self.lockfile.close()
            raise RuntimeError('Run exactly one Gunicorn worker per Narsika data directory.')
        # A killed process cannot execute TemporaryDirectory cleanup.
        for path in (Path(app.config['DATA_DIR'])/'runs').glob('job-*'):
            if path.is_dir() and not path.is_symlink():shutil.rmtree(path)
        self.stop=threading.Event()
        self.pool=concurrent.futures.ThreadPoolExecutor(max_workers=app.config['JOB_WORKERS'])
        self.futures={}
        with app.app_context():
            for run in OperationRun.query.filter_by(status='RUNNING').all():
                run.status='INTERRUPTED';run.error_code='PROCESS_RESTART';run.finished_at=now()
                run.output=(run.output or '')+'\nProcess restarted. Check the target for partial changes before rerunning.'
            db.session.commit()
        self.thread=threading.Thread(target=self.loop,name='narsika-dispatch',daemon=True);self.thread.start()
    def loop(self):
        while not self.stop.wait(.4):
            try:
                with self.app.app_context():
                    self.futures={k:v for k,v in self.futures.items() if not v.done()}
                    self.dispatch_pending()
                    from .scheduling import tick
                    if time.monotonic()-getattr(self,'last_schedule_tick',0)>=1:
                        tick();self.last_schedule_tick=time.monotonic()
            except Exception:self.app.logger.error('Job dispatcher encountered an error.')
    def dispatch_pending(self):
        """Keep target order without occupying a second worker for the same device."""
        capacity=max(0,self.app.config['JOB_WORKERS']-len(self.futures))
        active_targets={row.device_id for row in OperationRun.query.filter_by(status='RUNNING')
                        if row.device_id is not None}
        for run in OperationRun.query.filter_by(status='PENDING').order_by(OperationRun.id).limit(32).all():
            if run.cancel_requested:
                run.status='CANCELLED';run.finished_at=now();db.session.commit();continue
            if not capacity or run.device_id is not None and run.device_id in active_targets:
                continue
            run.status='RUNNING';run.started_at=now();db.session.commit()
            self.futures[run.id]=self.pool.submit(self.execute,run.id)
            if run.device_id is not None:active_targets.add(run.device_id)
            capacity-=1
    def execute(self,ident):
        with self.app.app_context():
            run=db.session.get(OperationRun,ident)
            try:
                if run.cancel_requested:
                    run.status='CANCELLED';run.finished_at=now()
                    run.output=((run.output or '')+'\nCancelled before network execution.')[-100000:]
                    audit('Run '+run.kind,str(run.device_id or run.id),'cancelled',actor_id=run.requested_by_id)
                    db.session.commit();return
                user=db.session.get(User,run.requested_by_id)
                if not user or not user.is_active or user.role not in ('ADMIN','OPERATOR') or user.must_change_password:fail('Run owner is no longer authorized.','FORBIDDEN',403)
                parameters=decrypt(run.encrypted_parameters)
                version=parameters.pop('_actor_session_version',None)
                if version is not None and version!=user.session_version:
                    fail('Account access changed after this run was queued. Review and submit it again.','AUTHORIZATION_CHANGED',409)
                device=db.session.get(Device,run.device_id) if run.device_id else None
                if run.kind!='discovery' and (not device or device.archived_at):fail('Target device is unavailable.','NOT_FOUND',404)
                if device and parameters.pop('_target',None)!=target_identity(device):
                    fail('Target connection settings changed after this run was queued. Review and submit it again.','TARGET_CHANGED',409)
                from .scheduling import preflight
                preflight(parameters)
                if run.kind=='discovery':
                    from .discovery import scan
                    detail=scan(run,parameters)
                elif run.kind=='backup':
                    from .backups import capture
                    from .network import device_lock
                    with device_lock(device.id):backup=capture(device,run.id)
                    db.session.commit();detail=f'Configuration backup {backup.id} saved with SHA-256 verification.'
                elif run.kind=='firewall':
                    from .firewall import execute
                    detail=execute(run,parameters,device,user)
                else:
                    from .automation import run_ansible
                    detail=run_ansible(run,parameters,device)
                if run.kind!='firewall':db.session.refresh(run)
                if run.kind!='firewall':run.status='CANCELLED' if run.cancel_requested else 'SUCCESS'
                run.progress=100 if run.status=='SUCCESS' else run.progress
            except APIError as ex:
                db.session.rollback();run=db.session.get(OperationRun,ident)
                if getattr(ex,'retryable_before_network',False):
                    # Only the connection-lock acquisition can grant this retry.
                    # An ambiguous or partially applied operation is never replayed.
                    run.status='PENDING';run.started_at=None;run.error_code=None
                    run.output='Waiting for an available device connection. No commands have been sent.'
                    db.session.commit();return
                run.status='FAILED';run.error_code=ex.code;detail=ex.message
            except Exception as ex:
                db.session.rollback();run=db.session.get(OperationRun,ident)
                run.status='FAILED';run.error_code='INTERNAL_ERROR';detail='Operation failed. Inspect server capabilities and target connectivity.'
                self.app.logger.error('Run %s failed: %s',ident,type(ex).__name__)
            run.output=((run.output or '')+'\n'+detail)[-100000:];run.finished_at=now()
            audit('Run '+run.kind,str(run.device_id or run.id),run.status.lower(),detail,actor_id=run.requested_by_id)
            db.session.commit()
    def close(self):
        self.stop.set();self.thread.join(timeout=2);self.pool.shutdown(wait=True,cancel_futures=True)
        fcntl.flock(self.lockfile,fcntl.LOCK_UN);self.lockfile.close()

    def available(self):
        return self.thread.is_alive() and not self.stop.is_set()

def enqueue(kind,device_id,user_id,parameters):
    from sqlalchemy import text
    if device_id:
        parameters={**parameters,'_target':target_identity(db.session.get(Device,device_id))}
    user=db.session.get(User,user_id)
    if user:
        parameters={**parameters,'_actor_session_version':user.session_version}
    # One SQLite statement reserves capacity even across simultaneous HTTP threads.
    ident=db.session.execute(text("""INSERT INTO operation_run
        (kind,device_id,requested_by_id,encrypted_parameters,status,output,progress,cancel_requested,created_at)
        SELECT :kind,:device,:actor,:parameters,'PENDING','',0,0,:created
        WHERE (SELECT COUNT(*) FROM operation_run WHERE status IN ('PENDING','RUNNING')) < 32
        RETURNING id"""),dict(kind=kind,device=device_id,actor=user_id,
                              parameters=encrypt(parameters),created=now())).scalar()
    if ident is None:fail('The operation queue is full. Retry later.','BUSY',429)
    return db.session.get(OperationRun,ident)

def target_identity(device):
    result={key:getattr(device,key) for key in ('ip_address','ssh_port','platform','credential_id')}
    profile=getattr(device,'credential',None)
    if profile:
        result['credential_revision']=hashlib.sha256((profile.username+'\0'+profile.encrypted_secret).encode()).hexdigest()
    return result
