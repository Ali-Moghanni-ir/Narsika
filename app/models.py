"""Additive models: original model names and legacy credential columns remain."""
from datetime import datetime, timezone
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()

def now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False)
    password = db.Column(db.String(255), default='')
    password_hash = db.Column(db.Text, nullable=False, default='')
    name = db.Column(db.String(100), default='')
    role = db.Column(db.String(20), default='ADMIN', nullable=False)
    must_change_password = db.Column(db.Boolean, default=True, nullable=False)
    disabled_at = db.Column(db.String(40))
    session_version = db.Column(db.Integer, default=1, nullable=False)
    created_at = db.Column(db.String(40), default=now)
    logs = db.relationship('AuditLog', backref='admin', lazy=True)
    @property
    def is_active(self):
        return self.disabled_at is None
    def set_password(self, value):
        self.password_hash = generate_password_hash(value, method='scrypt')
        self.password = ''
        self.session_version = (self.session_version or 0) + 1
    def check_password(self, value):
        encoded = self.password_hash or self.password
        if encoded.startswith(('$2a$', '$2b$', '$2y$')):
            import bcrypt
            try:
                return bcrypt.checkpw(value.encode()[:72], encoded.encode())
            except ValueError:
                return False
        try:
            return check_password_hash(encoded, value)
        except (ValueError, TypeError):
            return False
    def public(self):
        return dict(id=self.id, username=self.username, name=self.name or self.username, role=self.role,
                    must_change_password=self.must_change_password, disabled_at=self.disabled_at)

class Group(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    created_at = db.Column(db.String(40), default=now)
    archived_at = db.Column(db.String(40))
    devices = db.relationship('Device', backref='group', lazy=True)
    def public(self):
        return dict(id=self.id, name=self.name)

class Credential(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    username = db.Column(db.String(100), nullable=False)
    kind = db.Column(db.String(20), nullable=False)
    encrypted_secret = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.String(40), default=now)
    def public(self):
        return dict(id=self.id, name=self.name, username=self.username, kind=self.kind)

class Device(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    ip_address = db.Column(db.String(50), nullable=False, index=True)
    username = db.Column(db.String(50), default='')
    password = db.Column(db.String(100), default='')
    os_type = db.Column(db.String(50), default='cisco_ios')
    group_id = db.Column(db.Integer, db.ForeignKey('group.id'))
    credential_id = db.Column(db.Integer, db.ForeignKey('credential.id'))
    snmp_credential_id = db.Column(db.Integer, db.ForeignKey('credential.id'))
    platform = db.Column(db.String(20), default='cisco')
    ssh_port = db.Column(db.Integer, default=22)
    snmp_port = db.Column(db.Integer, default=161)
    model = db.Column(db.String(100), default='')
    notes = db.Column(db.Text, default='')
    health_json = db.Column(db.JSON)
    archived_at = db.Column(db.String(40))
    created_at = db.Column(db.String(40), default=now)
    credential = db.relationship('Credential', foreign_keys=[credential_id])
    snmp_credential = db.relationship('Credential', foreign_keys=[snmp_credential_id])
    def public(self):
        return dict(id=self.id, name=self.name, ip_address=self.ip_address, platform=self.platform,
                    ssh_port=self.ssh_port, snmp_port=self.snmp_port, model=self.model or '', notes=self.notes or '',
                    group_id=self.group_id, group=self.group.name if self.group else '',
                    credential_id=self.credential_id, credential=self.credential.name if self.credential else '',
                    snmp_credential_id=self.snmp_credential_id, health=self.health_json, archived_at=self.archived_at)

class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=lambda:datetime.now(timezone.utc).replace(tzinfo=None))
    admin_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    target_ip = db.Column(db.String(50), nullable=False)
    action_type = db.Column(db.String(100), nullable=False)
    status = db.Column(db.String(20), nullable=False)
    output = db.Column(db.Text)
    encrypted_output = db.Column(db.Text)

class AuditEvent(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    actor_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    actor = db.Column(db.String(100), nullable=False)
    action = db.Column(db.String(100), nullable=False)
    target = db.Column(db.String(150), default='')
    result = db.Column(db.String(20), default='success')
    detail = db.Column(db.Text, default='')
    created_at = db.Column(db.String(40), default=now, index=True)
    def public(self):
        return dict(id=self.id, actor=self.actor, action=self.action, target=self.target,
                    result=self.result, detail=self.detail, created_at=self.created_at)

class OperationRun(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(30), nullable=False)
    status = db.Column(db.String(20), default='PENDING', index=True)
    device_id = db.Column(db.Integer, db.ForeignKey('device.id'))
    requested_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    encrypted_parameters = db.Column(db.Text, nullable=False)
    output = db.Column(db.Text, default='')
    progress = db.Column(db.Integer, default=0)
    cancel_requested = db.Column(db.Boolean, default=False)
    error_code = db.Column(db.String(50))
    created_at = db.Column(db.String(40), default=now)
    started_at = db.Column(db.String(40))
    finished_at = db.Column(db.String(40))
    def public(self):
        return {k:getattr(self,k) for k in ('id','kind','status','device_id','requested_by_id','output','progress','error_code','created_at','started_at','finished_at','cancel_requested')}

class Backup(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.Integer, db.ForeignKey('device.id'), nullable=False)
    operation_run_id = db.Column(db.Integer, db.ForeignKey('operation_run.id'))
    path = db.Column(db.String(200), nullable=False)
    checksum = db.Column(db.String(64), nullable=False)
    size_bytes = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.String(40), default=now)
    archived_at = db.Column(db.String(40))
    def public(self):
        return {k:getattr(self,k) for k in ('id','device_id','operation_run_id','checksum','size_bytes','created_at','archived_at')}

class Playbook(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False, index=True)
    title = db.Column(db.String(150), nullable=False)
    vendor = db.Column(db.String(20), default='Any')
    path = db.Column(db.Text, nullable=False)
    defaults = db.Column(db.JSON, default=dict)
    version = db.Column(db.Integer, default=1)
    active = db.Column(db.Boolean, default=True)
    builtin = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.String(40), default=now)
    def public(self):
        return {k:getattr(self,k) for k in ('id','name','title','vendor','defaults','version','builtin')}

class DiscoveryScan(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    run_id = db.Column(db.Integer, db.ForeignKey('operation_run.id'), unique=True)
    cidr = db.Column(db.String(50), nullable=False)
    ssh_port = db.Column(db.Integer, default=22)
    credential_id = db.Column(db.Integer, db.ForeignKey('credential.id'))

class DiscoveryCandidate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    scan_id = db.Column(db.Integer, db.ForeignKey('discovery_scan.id'))
    ip_address = db.Column(db.String(50), nullable=False)
    vendor_hint = db.Column(db.String(20), default='unknown')
    verified_platform = db.Column(db.String(20))
    verified_credential_id = db.Column(db.Integer, db.ForeignKey('credential.id'))
    verified_at = db.Column(db.String(40))
    imported_device_id = db.Column(db.Integer, db.ForeignKey('device.id'))
    def public(self):
        return {k:getattr(self,k) for k in ('id','scan_id','ip_address','vendor_hint','verified_platform','verified_credential_id','verified_at','imported_device_id')}

class Setting(db.Model):
    key = db.Column(db.String(100), primary_key=True)
    value = db.Column(db.JSON, nullable=False)

class LoginAttempt(db.Model):
    key = db.Column(db.String(64), primary_key=True)
    count = db.Column(db.Integer, default=0)
    reset_at = db.Column(db.Float, nullable=False)

class RunArtifact(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    run_id = db.Column(db.Integer, db.ForeignKey('operation_run.id'), nullable=False)
    name = db.Column(db.String(180), nullable=False)
    path = db.Column(db.String(100), nullable=False)
    size_bytes = db.Column(db.Integer, nullable=False)
    checksum = db.Column(db.String(64), nullable=False)
    def public(self):
        return dict(id=self.id,run_id=self.run_id,name=self.name,size_bytes=self.size_bytes,checksum=self.checksum)
