"""Disposable UI fixture server, NEVER a deployment entrypoint.

Run: python -m tests.ui_preview is not required; use PYTHONPATH=. python tests/ui_preview.py.
All target reads are fixtures; network connections and workers are disabled.
The product itself never calls or imports this module.
"""
import copy
import tempfile
from pathlib import Path
from cryptography.fernet import Fernet
from flask import redirect, session
from flask_login import login_user
from app import create_app
from app.models import db, Device, User
from app.security import csrf_token, APIError
from app.services import firewall, network


def main():
    with tempfile.TemporaryDirectory(prefix='narsika-ui-fixture-') as directory:
        app=create_app(dict(TESTING=True, START_WORKER=False, SECRET_KEY='ui-fixture-only-'+'x'*40,
            ENCRYPTION_KEY=Fernet.generate_key().decode(), DATA_DIR=directory,
            BACKUP_DIR=str(Path(directory)/'backups'),SQLALCHEMY_DATABASE_URI='sqlite:///'+directory+'/fixture.db',
            ADMIN_PASSWORD='ui-fixture-password-not-for-deployment'))
        with app.app_context():
            user=User.query.first();user.must_change_password=False
            db.session.add_all([Device(name='UI TEST · Edge router',ip_address='10.0.0.1',platform='mikrotik'),
                                Device(name='UI TEST · Distribution switch',ip_address='10.0.0.2',platform='cisco')])
            db.session.commit()
        def unavailable(*args,**kwargs):
            raise APIError('UI fixture: real network execution is disabled.','FIXTURE_ONLY',503)
        network.connection=unavailable
        network.export_configuration=unavailable
        def fixture_state(device):
            if device.platform=='mikrotik':
                rows=firewall.parse_routeros('\n'.join([
                    '/ip firewall filter add chain=input action=accept connection-state=established,related comment="UI fixture: established connections"',
                    '/ip firewall filter add chain=input action=accept protocol=tcp dst-port=22 src-address=10.0.0.0/24 comment="UI fixture: management"',
                    '/ip firewall filter add chain=forward action=drop connection-state=invalid comment="UI fixture: invalid traffic"',
                    '/ip firewall filter add chain=input action=drop comment="UI fixture: default deny"']))
                result=dict(rules=rows,acls={},bindings=[])
            else:
                acls=firewall.parse_cisco('Extended IP access list UI_TEST\n    100 permit ip any any')
                result=dict(rules=[dict(acl='UI_TEST',**r) for r in acls['UI_TEST']],acls=acls,bindings=['interface GigabitEthernet0/1 / ip access-group UI_TEST in'])
            result.update(source_ip_hint='10.0.0.100',captured_at='2026-09-12T00:00:00Z')
            result['fingerprint']=firewall.digest(result)
            return copy.deepcopy(result)
        firewall.read_state=fixture_state
        @app.get('/ui-test-session')
        def fixture_session():
            login_user(User.query.first());session['version']=User.query.first().session_version;csrf_token()
            return redirect('/firewall.html')
        @app.after_request
        def fixture_banner(response):
            if response.mimetype=='text/html':
                response.set_data(response.get_data(as_text=True).replace('<main id="main">',
                    '<main id="main"><div style="padding:8px 12px;margin-bottom:16px;border:1px solid #675628;color:#e5c979;font-size:11px">ISOLATED UI TEST FIXTURES · NO LIVE NETWORK · EXECUTION DISABLED</div>'))
            return response
        app.run(host='127.0.0.1',port=5187,debug=False,use_reloader=False)


if __name__=='__main__':main()
