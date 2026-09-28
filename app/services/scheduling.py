"""Persisted calendar rules and atomic dispatch into the existing operation queue."""
import hashlib
import json
import math
import threading
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from sqlalchemy import text as sql
from sqlalchemy.orm import selectinload
from ..models import db, ScheduledTask, ScheduleOccurrence, ScheduleRun, OperationRun, Device, Playbook, now
from ..security import APIError, fail, integer, text, decrypt, encrypt, audit
from .jobs import enqueue, target_identity
from . import catalog

UTC=timezone.utc
LOCK=threading.RLock()
GRACE_SECONDS=60

def utc(value):
    return datetime.fromtimestamp(value,UTC).isoformat().replace('+00:00','Z') if value is not None else None

def wall_time(value,zone):
    # Repeated local times use their first occurrence; nonexistent times are skipped.
    aware=value.replace(tzinfo=zone,fold=0)
    return aware.timestamp() if datetime.fromtimestamp(aware.timestamp(),zone).replace(tzinfo=None)==value else None

def rule_data(data):
    if not isinstance(data,dict) or set(data)-{'frequency','timezone','start_at','interval_hours','weekdays'}:
        fail('Send a valid schedule rule.')
    frequency=data.get('frequency')
    if frequency not in ('once','interval','daily','weekly'):fail('Choose once, interval, daily or weekly.')
    name=text(data.get('timezone'),'time zone',80)
    try:zone=ZoneInfo(name)
    except (ZoneInfoNotFoundError,ValueError):fail('Unknown IANA time zone. Use a name such as Asia/Tehran or UTC.')
    try:
        start=datetime.fromisoformat(data.get('start_at',''))
        if start.tzinfo is not None:start=start.astimezone(zone).replace(tzinfo=None)
        if not 1970<=start.year<=2099 or start.second or start.microsecond:raise ValueError()
        anchor=wall_time(start,zone)
        if anchor is None:raise ValueError()
    except (ValueError,TypeError,OverflowError):fail('Choose a valid local start date and time, to the minute. This time may not exist during a clock change.')
    result={'frequency':frequency,'timezone':name,'start_at':start.isoformat(timespec='minutes')}
    if frequency=='interval':result['interval_hours']=integer(data.get('interval_hours'),'interval hours',1,720)
    if frequency=='weekly':
        days=data.get('weekdays')
        if not isinstance(days,list) or not 1<=len(days)<=7:fail('Select at least one weekday.')
        result['weekdays']=sorted(set(integer(day,'weekday',0,6) for day in days))
    return result

def next_time(rule,after):
    zone=ZoneInfo(rule['timezone']);start=datetime.fromisoformat(rule['start_at'])
    anchor=wall_time(start,zone);frequency=rule['frequency']
    if frequency=='once':return anchor if anchor>after else None
    if frequency=='interval':
        seconds=rule['interval_hours']*3600
        return anchor+max(0,math.floor((after-anchor)/seconds)+1)*seconds
    date=max(start.date(),datetime.fromtimestamp(after,zone).date())
    for offset in range(370):
        day=date+timedelta(days=offset)
        if frequency=='weekly' and day.weekday() not in rule['weekdays']:continue
        candidate=wall_time(datetime.combine(day,start.time()),zone)
        if candidate is not None and candidate>=anchor and candidate>after:return candidate
    fail('No valid next execution time was found.')

def upcoming(rule,after=None,count=3):
    cursor=time.time() if after is None else after;result=[]
    for _ in range(count):
        cursor=next_time(rule,cursor)
        if cursor is None:break
        result.append({'utc':utc(cursor),'local':datetime.fromtimestamp(cursor,ZoneInfo(rule['timezone'])).isoformat()})
    return result

def definition(data):
    from ..api import get, prepare_run
    kind=data.get('kind')
    if kind not in ('backup','playbook'):fail('Schedules support Backup and Playbook operations.')
    ids=data.get('device_ids')
    if not isinstance(ids,list) or not 1<=len(ids)<=32:fail('Select between 1 and 32 devices.')
    ids=[integer(i,'device ID',1,2**63-1) for i in ids]
    if len(set(ids))!=len(ids):fail('Each device can be selected only once.')
    result={'targets':[]}
    request={'variables':data.get('variables',{}),'playbook_id':data.get('playbook_id')}
    for ident in ids:
        device=get(Device,ident)
        params=prepare_run(kind,device,request)
        result['targets'].append({'id':ident,'name':device.name,'identity':target_identity(device)})
    result['parameters']=params
    if kind=='playbook':
        book=get(Playbook,params['playbook_id'])
        result['playbook']={'id':book.id,'name':book.name,'version':book.version,'sha256':hashlib.sha256(catalog.source(book).read_bytes()).hexdigest()}
    if len(json.dumps(result))>64000:fail('Schedule variables are too large.')
    return result

def validate_definition(task):
    from ..api import prepare_run
    owner=task.owner
    if not owner or not owner.is_active or owner.must_change_password or owner.role not in ('ADMIN','OPERATOR'):
        fail('The task owner is no longer authorized. Restore access and review this task.','AUTHORIZATION_CHANGED',409)
    saved=decrypt(task.encrypted_definition)
    if task.kind=='playbook':
        expected=saved['playbook'];book=db.session.get(Playbook,expected['id'])
        if not book or not book.active or book.version!=expected['version'] or hashlib.sha256(catalog.source(book).read_bytes()).hexdigest()!=expected['sha256']:
            fail('The playbook version or content changed. Edit and review this task.','PLAYBOOK_CHANGED',409)
    for target in saved['targets']:
        device=db.session.get(Device,target['id'])
        if not device or device.archived_at or target_identity(device)!=target['identity']:
            fail('A target or its connection credentials changed. Edit and review this task.','TARGET_CHANGED',409)
        prepare_run(task.kind,device,saved['parameters'])
    return saved

def preflight(parameters):
    ident=parameters.pop('_scheduled_task',None)
    if ident is None:return
    revision=parameters.pop('_schedule_revision',None)
    task=db.session.get(ScheduledTask,ident)
    if not task or task.revision!=revision:fail('Scheduled task changed after dispatch. Review before rerunning.','SCHEDULE_CHANGED',409)
    validate_definition(task)

def occurrence_public(row):
    runs=[{'device_id':link.device_id,'device_name':link.device_name,**link.run.public(include_output=False)} for link in row.runs]
    states=[run['status'] for run in runs]
    status=row.status
    if states:
        status=('RUNNING' if 'RUNNING' in states else 'QUEUED') if any(s in ('PENDING','RUNNING') for s in states) else 'SUCCESS' if all(s=='SUCCESS' for s in states) else 'CANCELLED' if all(s=='CANCELLED' for s in states) else 'PARTIAL' if 'SUCCESS' in states else 'FAILED'
    return {'id':row.id,'task_id':row.task_id,'scheduled_for':utc(row.scheduled_for),'source':row.source,'status':status,'detail':row.detail,'revision':row.revision,'created_at':row.created_at,'runs':runs,'success_count':states.count('SUCCESS'),'target_count':len(runs)}

def occurrence_query():
    return ScheduleOccurrence.query.options(selectinload(ScheduleOccurrence.runs).joinedload(ScheduleRun.run).defer(OperationRun.output).defer(OperationRun.encrypted_parameters))

def task_public(task,details=False,last=None,lookup_last=True):
    saved=decrypt(task.encrypted_definition)
    if lookup_last:last=occurrence_query().filter_by(task_id=task.id).order_by(ScheduleOccurrence.id.desc()).first()
    result={'id':task.id,'name':task.name,'owner_id':task.owner_id,'owner':task.owner.username,'kind':task.kind,'enabled':task.enabled,'revision':task.revision,'rule':task.rule,'next_due':utc(task.next_due) if task.enabled else None,'attention':task.attention,'targets':[{'id':d['id'],'name':d['name']} for d in saved['targets']],'playbook':saved.get('playbook'),'created_at':task.created_at,'updated_at':task.updated_at,'last_occurrence':occurrence_public(last) if last else None}
    if details:result['variables']=saved['parameters'].get('variables',{})
    return result

def has_active(task):
    return db.session.query(ScheduleRun.id).join(ScheduleOccurrence,ScheduleOccurrence.id==ScheduleRun.occurrence_id).join(OperationRun,OperationRun.id==ScheduleRun.run_id).filter(ScheduleOccurrence.task_id==task.id,OperationRun.status.in_(('PENDING','RUNNING'))).first() is not None

def dispatch(task,token,due,source,actor_id=None):
    """Caller owns a SQLite write reservation. Queue + receipt commit together."""
    existing=ScheduleOccurrence.query.filter_by(task_id=task.id,token=token).first()
    if existing:return existing,True
    row=ScheduleOccurrence(task_id=task.id,token=token,scheduled_for=due,source=source,status='QUEUED',revision=task.revision)
    db.session.add(row);db.session.flush()
    if has_active(task):row.status='SKIPPED_OVERLAP';row.detail='The previous occurrence is still queued or running.'
    else:
        try:saved=validate_definition(task)
        except APIError as ex:
            task.enabled=False;task.attention=ex.message;row.status='BLOCKED';row.detail=ex.message
        else:
            used=OperationRun.query.filter(OperationRun.status.in_(('PENDING','RUNNING'))).count()
            if used+len(saved['targets'])>32:
                row.status='SKIPPED_CAPACITY';row.detail='The operation queue had insufficient room for every target. No targets were submitted.'
            else:
                for target in saved['targets']:
                    run=enqueue(task.kind,target['id'],task.owner_id,{**saved['parameters'],'_scheduled_task':task.id,'_schedule_revision':task.revision})
                    db.session.add(ScheduleRun(occurrence_id=row.id,device_id=target['id'],device_name=target['name'],run_id=run.id))
    audit('Scheduled task '+source,str(task.id),row.status.lower(),row.detail or task.name,actor_id=actor_id or task.owner_id)
    db.session.flush()
    return row,False

def tick(moment=None):
    moment=time.time() if moment is None else moment
    # Only the process holding the existing worker file lock invokes this loop.
    with LOCK:
        if not ScheduledTask.query.filter(ScheduledTask.enabled.is_(True),ScheduledTask.next_due<=moment).first():return
        db.session.execute(sql('BEGIN IMMEDIATE'))
        try:
            rows=ScheduledTask.query.filter(ScheduledTask.enabled.is_(True),ScheduledTask.next_due<=moment).order_by(ScheduledTask.next_due,ScheduledTask.id).limit(50).populate_existing().all()
            for task in rows:
                due=task.next_due
                token='due:'+str(int(due))
                if moment-due>GRACE_SECONDS:
                    if not ScheduleOccurrence.query.filter_by(task_id=task.id,token=token).first():
                        db.session.add(ScheduleOccurrence(task_id=task.id,token=token,scheduled_for=due,source='scheduled',status='MISSED',detail='Past-due executions were skipped; no catch-up work was submitted.',revision=task.revision))
                        audit('Scheduled task missed',str(task.id),'missed',task.name,actor_id=task.owner_id)
                else:dispatch(task,token,due,'scheduled')
                task.next_due=next_time(task.rule,moment)
                if task.next_due is None:task.enabled=False
            db.session.commit()
        except Exception:db.session.rollback();raise
