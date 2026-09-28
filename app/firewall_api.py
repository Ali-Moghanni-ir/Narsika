"""Read, review and consume firewall intents without accepting executable YAML."""
import time
import uuid
from flask import Blueprint, request, current_app
from flask_login import current_user
from sqlalchemy import update
from .api import get, ok
from .models import db, Device, FirewallReview, OperationRun
from .security import require, payload, encrypt, decrypt, audit, fail
from .services import firewall as fw
from .services.network import device_lock
from .services.jobs import enqueue, target_identity, worker_available

firewall_api = Blueprint('firewall_api', __name__)


def receipt(ident):
    row = db.session.get(FirewallReview, ident)
    if not row:
        fail('Review not found.', 'NOT_FOUND', 404)
    if row.actor_id != current_user.id and current_user.role != 'ADMIN':
        fail('This review belongs to another user.', 'FORBIDDEN', 403)
    return row


@firewall_api.get('/capabilities')
@require()
def capabilities():
    return ok(dict(presets=fw.PRESETS, max_items=fw.MAX_ITEMS, review_ttl=fw.TTL,
                   automatic_rollback=False, scope='IPv4 filter additions',
                   role=current_user.role))


@firewall_api.post('/devices/<int:ident>/refresh')
@require()
def refresh(ident):
    payload(set())
    device = get(Device, ident)
    with device_lock(device.id):
        state = fw.read_state(device)
    state = fw.annotate_receipt_matches(state, device)
    return ok(dict(device=device.public(), state=state))


@firewall_api.post('/devices/<int:ident>/reviews')
@require()
def review(ident):
    data = payload({'source_ip', 'changes'}, ('source_ip', 'changes'))
    device = get(Device, ident)
    # Reject malformed input before connecting to a network device.
    fw.normalize(data, device)
    with device_lock(device.id):
        state = fw.read_state(device)
    plan = fw.compile_plan(data, device, state, current_user.role)
    plan['reviewer_session_version'] = current_user.session_version
    row = FirewallReview(id=uuid.uuid4().hex, device_id=device.id, actor_id=current_user.id,
                         encrypted_plan=encrypt(plan), checksum=fw.digest(plan),
                         expires_at=time.time()+fw.TTL, status='REVIEWED')
    db.session.add(row)
    audit('Firewall reviewed', device.ip_address, detail='Review '+row.id+'; risk '+plan['risk'])
    db.session.commit()
    return ok(fw.public_review(row), 201)


@firewall_api.get('/reviews/<ident>')
@require()
def review_detail(ident):
    row = receipt(ident)
    data = fw.public_review(row)
    run = db.session.get(OperationRun, row.run_id) if row.run_id else None
    data['run'] = run.public() if run else None
    if run:
        data['status'] = row.status if run.status=='RUNNING' else run.status
    elif row.expires_at < time.time():
        data['status'] = 'EXPIRED'
    return ok(data)


@firewall_api.get('/history')
@require()
def history():
    query = FirewallReview.query
    if current_user.role != 'ADMIN':
        query = query.filter_by(actor_id=current_user.id)
    if request.args.get('device'):
        query = query.filter_by(device_id=get(Device, request.args['device'], active=False).id)
    items = []
    for row in query.order_by(FirewallReview.created_at.desc()).limit(40):
        plan = decrypt(row.encrypted_plan)
        run = db.session.get(OperationRun, row.run_id) if row.run_id else None
        status = (row.status if run.status=='RUNNING' else run.status) if run else ('EXPIRED' if row.expires_at < time.time() else row.status)
        items.append(dict(id=row.id, device_id=row.device_id, created_at=row.created_at,
                          status=status, risk=plan['risk'], count=len(plan['items']), run_id=row.run_id))
    return ok(dict(items=items))


@firewall_api.post('/reviews/<ident>/apply')
@require('operate')
def apply_review(ident):
    data = payload({'checksum', 'target_confirmed', 'source_confirmed', 'risk_ack',
                    'recovery_saved', 'no_rollback_ack', 'oob_confirmed', 'device_name', 'phrase'},
                   ('checksum',))
    row = receipt(ident)
    if row.actor_id != current_user.id:
        fail('Create your own review before applying.', 'FORBIDDEN', 403)
    plan = decrypt(row.encrypted_plan)
    if data['checksum'] != row.checksum or fw.digest(plan) != row.checksum:
        fail('The reviewed plan does not match this receipt.', 'INVALID_REVIEW', 409)
    if row.run_id:
        return ok(dict(review_id=row.id, run=db.session.get(OperationRun, row.run_id).public(), reused=True))
    if not current_app.testing and not worker_available(current_app):
        fail('The operation worker is not running.', 'CAPABILITY_UNAVAILABLE', 503)
    if row.status != 'REVIEWED' or row.expires_at < time.time():
        fail('Review expired or already consumed. Review again.', 'REVIEW_EXPIRED', 409)
    device = get(Device, row.device_id)
    if plan['target'] != target_identity(device):
        fail('Device connection settings changed. Review again.', 'TARGET_CHANGED', 409)
    if plan['reviewer_session_version'] != current_user.session_version:
        fail('Account permissions changed. Review again.', 'REVIEW_EXPIRED', 409)
    if current_user.role != 'ADMIN' and (plan['risk'] in ('HIGH', 'LOCKOUT') or
                                       any(i['rule']['service']=='custom' for i in plan['items'])):
        fail('An administrator must apply this change set.', 'FORBIDDEN', 403)
    for field in ('target_confirmed', 'source_confirmed', 'risk_ack', 'recovery_saved', 'no_rollback_ack'):
        if data.get(field) is not True:
            fail('Confirm the target, source, effects and recovery requirements.', 'CONFIRMATION_REQUIRED', 422)
    if plan['risk']=='LOCKOUT' and (data.get('oob_confirmed') is not True or
            data.get('device_name') != device.name or data.get('phrase') != 'DISCONNECT'):
        fail('Confirm out-of-band access, type the device name and DISCONNECT.', 'CONFIRMATION_REQUIRED', 422)
    claimed = db.session.execute(update(FirewallReview).where(
        FirewallReview.id==row.id, FirewallReview.status=='REVIEWED', FirewallReview.run_id.is_(None)
    ).values(status='QUEUED')).rowcount
    if claimed != 1:
        db.session.rollback()
        fail('Another request already consumed this review.', 'CONFLICT', 409)
    run = enqueue('firewall', device.id, current_user.id,
                  dict(review_id=row.id, session_version=current_user.session_version))
    row.run_id = run.id
    audit('Firewall confirmed', device.ip_address, 'queued',
          'Review '+row.id+'; checksum '+row.checksum+'; risk '+plan['risk']+
          '; explicit acknowledgements accepted; automatic rollback unavailable')
    db.session.commit()
    return ok(dict(review_id=row.id, run=run.public(), reused=False), 202)
