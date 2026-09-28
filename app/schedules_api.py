"""Reviewed schedule CRUD, preview and idempotent manual execution."""
import time
import uuid
from flask import Blueprint, current_app, request
from flask_login import current_user
from sqlalchemy import text as sql, func, select
from sqlalchemy.orm import joinedload
from .models import db, ScheduledTask, ScheduleOccurrence, now
from .security import APIError, require, payload, text, integer, fail, encrypt, decrypt, audit
from .api import ok, get, boolean
from .services import scheduling as S
from .services.jobs import worker_available

schedules_api=Blueprint('schedules_api',__name__)
FIELDS={'name','kind','device_ids','playbook_id','variables','rule','enabled','task_id','revision'}

def manage(task):
    if current_user.role!='ADMIN' and task.owner_id!=current_user.id:fail('Only the owner or an administrator can manage this task.','FORBIDDEN',403)

@schedules_api.get('')
@require()
def tasks():
    rows=ScheduledTask.query.options(joinedload(ScheduledTask.owner)).order_by(ScheduledTask.id.desc()).all()
    latest=select(func.max(ScheduleOccurrence.id)).group_by(ScheduleOccurrence.task_id)
    history={row.task_id:row for row in S.occurrence_query().filter(ScheduleOccurrence.id.in_(latest)).all()}
    return ok({'items':[S.task_public(row,last=history.get(row.id),lookup_last=False) for row in rows],'server_time':S.utc(time.time()),'worker_available':worker_available(current_app),'grace_seconds':S.GRACE_SECONDS,'limit':200})

@schedules_api.post('/preview')
@require('operate')
def preview():
    data=payload(FIELDS,('name','kind','device_ids','rule','enabled'))
    task=get(ScheduledTask,data['task_id']) if data.get('task_id') is not None else None
    if task:
        manage(task)
        if integer(data.get('revision'),'revision')!=task.revision:fail('This task changed. Reload it before editing.','CONFLICT',409)
    name=text(data['name'],'task name');rule=S.rule_data(data['rule'])
    enabled=boolean(data['enabled'],'enabled');saved=S.definition(data)
    times=S.upcoming(rule)
    if not times:fail('Choose a future start time for a one-time task.')
    token=encrypt({'key':uuid.uuid4().hex,'actor':current_user.id,'session':current_user.session_version,'expires':time.time()+600,'task_id':task.id if task else None,'revision':task.revision if task else None,'data':{'name':name,'kind':data['kind'],'rule':rule,'enabled':enabled},'definition':saved})
    return ok({'review_token':token,'upcoming':times,'targets':[{'id':d['id'],'name':d['name'],'ip_address':d['identity']['ip_address'],'platform':d['identity']['platform']} for d in saved['targets']],'playbook':saved.get('playbook'),'name':name,'kind':data['kind'],'timezone':rule['timezone'],'enabled':enabled,'expires_in':600})

@schedules_api.post('')
@require('operate')
def save():
    data=payload({'review_token'},('review_token',))
    token=data['review_token']
    if not isinstance(token,str) or len(token)>150000:fail('Invalid review.')
    try:receipt=decrypt(token)
    except APIError:fail('This review is invalid. Preview the task again.','INVALID_REVIEW',409)
    if not isinstance(receipt,dict) or receipt.get('actor')!=current_user.id or receipt.get('session')!=current_user.session_version or receipt.get('expires',0)<time.time():fail('Review expired or account changed. Preview again.','REVIEW_EXPIRED',409)
    if not all(k in receipt for k in ('key','task_id','revision','data','definition')):fail('Invalid review.')
    with S.LOCK:
        db.session.execute(sql('BEGIN IMMEDIATE'))
        task=get(ScheduledTask,receipt['task_id']) if receipt['task_id'] else None
        if task:
            db.session.refresh(task);manage(task)
            if task.last_review_token==receipt['key']:return ok(S.task_public(task))
            if task.revision!=receipt['revision']:fail('This task changed. Preview it again.','CONFLICT',409)
        else:
            existing=ScheduledTask.query.filter_by(creation_token=receipt['key']).first()
            if existing:return ok(S.task_public(existing))
            if ScheduledTask.query.count()>=200:fail('This installation supports up to 200 scheduled tasks.','CAPACITY',409)
        expected=receipt['definition'];body=receipt['data']
        # Revalidate target identities and source bytes immediately before saving.
        checked=S.definition({'kind':body['kind'],'device_ids':[d['id'] for d in expected['targets']],**expected['parameters']})
        if checked!=expected:fail('A target or playbook changed after preview. Preview again.','STALE_STATE',409)
        due=S.next_time(body['rule'],time.time())
        if due is None:fail('The start time has passed. Preview a future time.','REVIEW_EXPIRED',409)
        if task is None:
            task=ScheduledTask(owner_id=current_user.id,creation_token=receipt['key']);db.session.add(task)
        else:task.revision+=1
        task.name=body['name'];task.kind=body['kind'];task.rule=body['rule'];task.enabled=body['enabled']
        task.encrypted_definition=encrypt(expected);task.next_due=due;task.attention='';task.updated_at=now()
        task.last_review_token=receipt['key']
        db.session.flush();audit('Scheduled task saved',str(task.id),detail=task.name)
        db.session.commit()
        return ok(S.task_public(task),201)

@schedules_api.get('/<int:ident>')
@require()
def detail(ident):
    task=get(ScheduledTask,ident)
    return ok(S.task_public(task,details=current_user.role=='ADMIN' or current_user.role=='OPERATOR' and current_user.id==task.owner_id))

@schedules_api.post('/<int:ident>/state')
@require('operate')
def state(ident):
    data=payload({'enabled','revision'},('enabled','revision'))
    enabled=boolean(data['enabled'],'enabled')
    with S.LOCK:
        db.session.execute(sql('BEGIN IMMEDIATE'))
        task=get(ScheduledTask,ident);db.session.refresh(task);manage(task)
        if integer(data['revision'],'revision')!=task.revision:fail('This task changed. Reload it.','CONFLICT',409)
        if enabled:
            S.validate_definition(task)
            task.next_due=S.next_time(task.rule,time.time())
            if task.next_due is None:fail('This one-time task has ended. Edit its start time.')
        task.enabled=enabled;task.updated_at=now()
        audit('Scheduled task resumed' if enabled else 'Scheduled task paused',str(task.id),detail=task.name)
        db.session.commit();return ok(S.task_public(task))

@schedules_api.post('/<int:ident>/run')
@require('operate')
def run_now(ident):
    data=payload({'request_key','revision'},('request_key','revision'))
    key=text(data['request_key'],'request key',64)
    if len(key)<16:fail('Use a unique request key of at least 16 characters.')
    if not current_app.testing and not worker_available(current_app):fail('The operation worker is not running.','CAPABILITY_UNAVAILABLE',503)
    with S.LOCK:
        db.session.execute(sql('BEGIN IMMEDIATE'))
        task=get(ScheduledTask,ident);db.session.refresh(task);manage(task)
        if integer(data['revision'],'revision')!=task.revision:fail('This task changed. Reload it.','CONFLICT',409)
        row,reused=S.dispatch(task,'manual:'+key,time.time(),'manual',current_user.id)
        db.session.commit();return ok({'occurrence':S.occurrence_public(row),'reused':reused},200 if reused else 202)

@schedules_api.get('/<int:ident>/history')
@require()
def history(ident):
    get(ScheduledTask,ident)
    page=integer(request.args.get('page','1'),'page',1,1000000)
    query=S.occurrence_query().filter_by(task_id=ident)
    return ok({'items':[S.occurrence_public(row) for row in query.order_by(ScheduleOccurrence.id.desc()).offset((page-1)*25).limit(25)],'page':page,'total':query.count(),'page_size':25})
