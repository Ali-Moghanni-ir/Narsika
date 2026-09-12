import hashlib
import ipaddress
import json
import re
import secrets
from functools import wraps
from flask import current_app, request, session, has_request_context
from flask_login import current_user
from cryptography.fernet import Fernet, InvalidToken

class APIError(Exception):
    def __init__(self, message, code='VALIDATION_ERROR', status=422, fields=None):
        self.message, self.code, self.status, self.fields = message, code, status, fields or {}

def fail(message, code='VALIDATION_ERROR', status=422):
    raise APIError(message, code, status)

def encrypt(value):
    return Fernet(current_app.config['ENCRYPTION_KEY'].encode()).encrypt(json.dumps(value).encode()).decode()

def decrypt(value):
    try:
        return json.loads(Fernet(current_app.config['ENCRYPTION_KEY'].encode()).decrypt(value.encode()))
    except (InvalidToken, ValueError):
        fail('Unable to decrypt stored credentials. Restore the original encryption key.', 'KEY_ERROR', 503)

def csrf_token():
    if 'csrf' not in session:
        session['csrf'] = secrets.token_urlsafe(32)
    return session['csrf']

def require(level='read'):
    def decorate(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                fail('Sign in to continue.', 'UNAUTHENTICATED', 401)
            roles = {'read':{'VIEWER','OPERATOR','ADMIN'},'operate':{'OPERATOR','ADMIN'},'admin':{'ADMIN'}}
            if current_user.role not in roles[level]:
                fail('Your role cannot perform this action.', 'FORBIDDEN', 403)
            return fn(*args, **kwargs)
        return wrapped
    return decorate

def payload(allowed, required=()):
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        fail('Send a JSON object.', status=400)
    if set(data)-set(allowed):
        fail('Unknown fields: '+', '.join(sorted(set(data)-set(allowed))))
    if any(k not in data for k in required):
        fail('Required fields are missing.')
    return data

def integer(value, name, minimum=1, maximum=65535):
    if isinstance(value, bool) or not re.fullmatch(r'[0-9]+', str(value)):
        fail(f'{name} must be an integer.')
    n = int(value)
    if not minimum <= n <= maximum:
        fail(f'{name} must be between {minimum} and {maximum}.')
    return n

def text(value, name, length=100, required=True):
    if not isinstance(value, str) or len(value.strip())>length or (required and not value.strip()) or any(ord(c)<32 for c in value):
        fail(f'Invalid {name}.')
    return value.strip()

def address(value, enforce_scope=True):
    try:
        ip = ipaddress.IPv4Address(value)
    except (ValueError, TypeError):
        fail('Use a valid IPv4 address.')
    if enforce_scope:
        nets=[ipaddress.ip_network(n) for n in current_app.config['ALLOWED_NETWORKS']]
        if not any(ip in n for n in nets) or ip.is_multicast or ip.is_unspecified:
            fail('This address is outside NARSIKA_ALLOWED_NETWORKS.', 'TARGET_NOT_ALLOWED', 403)
    return str(ip)

def network(value):
    try:
        net = ipaddress.IPv4Network(value, strict=True)
    except (ValueError, TypeError):
        fail('Use a canonical IPv4 network in CIDR notation.')
    if net.num_addresses>current_app.config['SCAN_MAX_HOSTS']:
        fail('The scan range exceeds the configured host limit.')
    if not any(net.subnet_of(ipaddress.ip_network(n)) for n in current_app.config['ALLOWED_NETWORKS']):
        fail('The scan is outside NARSIKA_ALLOWED_NETWORKS.', 'TARGET_NOT_ALLOWED', 403)
    return net

def password_policy(value):
    if not isinstance(value,str) or not 12 <= len(value) <= 256:
        fail('Use a password of 12–256 characters.')
    return value

def audit(action, target='', result='success', detail='', actor_id=None):
    from .models import db, AuditEvent, User
    user=db.session.get(User,actor_id) if actor_id else (current_user if has_request_context() and current_user.is_authenticated else None)
    row=AuditEvent(actor_id=user.id if user else None, actor=user.username if user else 'anonymous',
                   action=action, target=str(target)[:150], result=result, detail=detail)
    db.session.add(row)
    return row
