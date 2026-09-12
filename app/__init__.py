import os
import secrets
from datetime import timedelta
from pathlib import Path
import ipaddress
import re
from contextlib import contextmanager
from flask import Flask, jsonify, request, session, redirect, g
from flask_login import LoginManager, current_user, logout_user
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError
from werkzeug.exceptions import HTTPException
from cryptography.fernet import Fernet
from .models import db, User, Setting, Credential
from .security import APIError, csrf_token, password_policy

ROOT = Path(__file__).resolve().parent.parent

@contextmanager
def startup_guard(app):
    import fcntl
    with (Path(app.config['DATA_DIR'])/'worker.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError('Stop the active Narsika process before migrating or starting another instance.') from None
        try:yield
        finally:fcntl.flock(lock,fcntl.LOCK_UN)

def create_app(config=None):
    app=Flask(__name__)
    data=Path(os.getenv('NARSIKA_DATA_DIR',str(ROOT/'instance'))).resolve()
    app.config.update(SECRET_KEY=os.getenv('NARSIKA_SECRET_KEY',''),ENCRYPTION_KEY=os.getenv('NARSIKA_ENCRYPTION_KEY',''),
        DATA_DIR=str(data),BACKUP_DIR=os.getenv('NARSIKA_BACKUP_DIR',str(data/'backups')),
        SQLALCHEMY_DATABASE_URI='sqlite:///'+str(data/'narsika.db'),SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SQLALCHEMY_ENGINE_OPTIONS={'connect_args':{'timeout':30}},SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax',SESSION_COOKIE_SECURE=os.getenv('NARSIKA_COOKIE_SECURE','false').lower()=='true',
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),MAX_CONTENT_LENGTH=2*1024*1024,MAX_FORM_MEMORY_SIZE=262144,
        ALLOWED_NETWORKS=os.getenv('NARSIKA_ALLOWED_NETWORKS','10.0.0.0/8,172.16.0.0/12,192.168.0.0/16').split(','),
        SCAN_MAX_HOSTS=int(os.getenv('NARSIKA_SCAN_MAX_HOSTS','256')),JOB_TIMEOUT=int(os.getenv('NARSIKA_JOB_TIMEOUT','300')),
        JOB_WORKERS=2,START_WORKER=os.getenv('NARSIKA_START_WORKER','true').lower()=='true',
        TRUST_PROXY_HOPS=int(os.getenv('NARSIKA_TRUST_PROXY_HOPS','0')),
        WEB_NETWORKS=os.getenv('NARSIKA_WEB_NETWORKS',''),
        ADMIN_USERNAME=os.getenv('NARSIKA_ADMIN_USERNAME','admin'),ADMIN_PASSWORD='')
    if config:app.config.update(config)
    # Bootstrap values must not remain in the environment inherited by child processes.
    os.environ.pop('NARSIKA_ADMIN_PASSWORD',None)
    os.environ.pop('NARSIKA_ADMIN_PASSWORD_BASE64',None)
    if len(app.config['SECRET_KEY'])<32:raise RuntimeError('Set NARSIKA_SECRET_KEY using configure.py.')
    Fernet(app.config['ENCRYPTION_KEY'].encode())
    app.config['ALLOWED_NETWORKS']=[str(ipaddress.IPv4Network(n.strip(),strict=True)) for n in app.config['ALLOWED_NETWORKS']]
    if not app.config['ALLOWED_NETWORKS']:raise RuntimeError('Configure at least one allowed IPv4 network.')
    if not 1<=app.config['SCAN_MAX_HOSTS']<=256:raise RuntimeError('NARSIKA_SCAN_MAX_HOSTS must be between 1 and 256.')
    if not 5<=app.config['JOB_TIMEOUT']<=300:raise RuntimeError('NARSIKA_JOB_TIMEOUT must be between 5 and 300 seconds.')
    if app.config['TRUST_PROXY_HOPS'] not in (0,1):raise RuntimeError('NARSIKA_TRUST_PROXY_HOPS must be 0 or 1.')
    web_networks = [ipaddress.IPv4Network(n.strip(), strict=True) for n in app.config['WEB_NETWORKS'].split(',') if n.strip()]
    if app.config['TRUST_PROXY_HOPS']:
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app=ProxyFix(app.wsgi_app,x_for=1,x_proto=1,x_host=0,x_port=0,x_prefix=0)
    for folder in (app.config['DATA_DIR'],app.config['BACKUP_DIR'],str(Path(app.config['DATA_DIR'])/'playbooks'),str(Path(app.config['DATA_DIR'])/'runs')):
        Path(folder).mkdir(parents=True,exist_ok=True,mode=0o700)
    Path(app.config['DATA_DIR'],'known_hosts').touch(mode=0o600,exist_ok=True)
    db.init_app(app)
    login=LoginManager(app)
    @login.user_loader
    def load_user(uid):
        try:return db.session.get(User,int(uid))
        except (ValueError,TypeError):return None
    @app.before_request
    def guards():
        g.request_id = secrets.token_hex(8)
        if web_networks:
            try:
                client = ipaddress.ip_address(request.remote_addr or '')
                permitted = any(client in net for net in web_networks)
            except ValueError:
                permitted = False
            if not permitted:
                raise APIError('Access is limited to configured management networks.', 'SOURCE_NOT_ALLOWED', 403)
        if current_user.is_authenticated and (not current_user.is_active or session.get('version')!=current_user.session_version):
            logout_user();session.clear()
        if request.method not in ('GET','HEAD','OPTIONS'):
            provided=request.headers.get('X-CSRFToken') or request.form.get('csrf_token')
            if not provided or not session.get('csrf') or not secrets.compare_digest(provided.encode(),session['csrf'].encode()):
                raise APIError('Session token missing or expired. Reload the page.','CSRF_FAILED',403)
        if current_user.is_authenticated and current_user.must_change_password and request.endpoint not in ('web.password','web.logout','api.session_info','static','healthz'):
            if request.path.startswith('/api/'):
                raise APIError('Change your password before continuing.','PASSWORD_CHANGE_REQUIRED',403)
            return redirect('/change-password')
    @app.after_request
    def headers(response):
        response.headers['X-Request-ID'] = getattr(g, 'request_id', secrets.token_hex(8))
        response.headers.update({'X-Content-Type-Options':'nosniff','X-Frame-Options':'DENY','Referrer-Policy':'same-origin',
            'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"})
        if not request.path.startswith('/static/'):
            response.headers['Cache-Control']='no-store'
        if request.is_secure:response.headers['Strict-Transport-Security']='max-age=31536000'
        return response
    @app.errorhandler(APIError)
    def api_error(error):
        db.session.rollback()
        return jsonify(error={'code':error.code,'message':error.message,'fields':error.fields,'request_id':getattr(g, 'request_id', '')}),error.status
    @app.errorhandler(IntegrityError)
    def conflict(error):
        db.session.rollback()
        return api_error(APIError('A record with these values already exists.','CONFLICT',409))
    @app.errorhandler(HTTPException)
    def http_error(error):
        if request.path.startswith('/api/'):
            return api_error(APIError(error.name,'HTTP_ERROR',error.code))
        return error
    @app.errorhandler(Exception)
    def internal_error(error):
        db.session.rollback()
        import traceback
        frames = ';'.join(f'{Path(frame.filename).name}:{frame.lineno}:{frame.name}' for frame in traceback.extract_tb(error.__traceback__))
        app.logger.error('Request failed id=%s type=%s frames=%s', getattr(g, 'request_id', ''), type(error).__name__, frames)
        return api_error(APIError('The operation could not be completed.','INTERNAL_ERROR',500))
    @app.get('/healthz')
    def healthz():
        from sqlalchemy import text
        db.session.execute(text('SELECT 1'))
        return jsonify(status='ok')
    app.context_processor(lambda:dict(csrf_token=csrf_token,static_root='/static'))
    with app.app_context(),startup_guard(app):
        @event.listens_for(db.engine,'connect')
        def sqlite_options(connection,record):
            connection.execute('PRAGMA foreign_keys=ON')
            connection.execute('PRAGMA busy_timeout=30000')
            connection.execute('PRAGMA secure_delete=ON')
        from .migration import migrate
        migrate()
        from .security import decrypt, encrypt
        keycheck=db.session.get(Setting,'internal_encryption_check')
        if keycheck:
            if decrypt(keycheck.value)!='narsika-key-check':raise RuntimeError('Invalid encryption key.')
        else:
            for profile in Credential.query.limit(1):decrypt(profile.encrypted_secret)
            db.session.add(Setting(key='internal_encryption_check',value=encrypt('narsika-key-check')))
            db.session.commit()
        if not User.query.first():
            if not app.config['ADMIN_PASSWORD']:
                raise RuntimeError('No administrator exists. Run the installer or tools/bootstrap.py from an interactive terminal.')
            if not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}',app.config['ADMIN_USERNAME']):raise RuntimeError('Invalid bootstrap admin username.')
            password_policy(app.config['ADMIN_PASSWORD'])
            user=User(username=app.config['ADMIN_USERNAME'],name=app.config['ADMIN_USERNAME'],role='ADMIN')
            user.set_password(app.config['ADMIN_PASSWORD'])
            db.session.add(user);db.session.commit()
        from .services.catalog import seed_catalog
        seed_catalog()
    app.config['ADMIN_PASSWORD']=''
    from .routes import web
    from .api import api
    app.register_blueprint(web);app.register_blueprint(api,url_prefix='/api')
    if app.config['START_WORKER']:
        from .services.jobs import JobManager
        app.extensions['jobs']=JobManager(app)
    return app
