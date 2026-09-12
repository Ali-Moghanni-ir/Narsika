#!/usr/bin/env python3
"""Offline account provisioning. Passwords are never sent through argv or environment."""
import argparse
import os
from pathlib import Path
import secrets
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from runtime import load_environment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reset-admin', metavar='USERNAME')
    args = parser.parse_args()
    load_environment()
    source = Path(os.getenv('NARSIKA_DATA_DIR', str(ROOT / 'instance'))) / 'narsika.db'
    if args.reset_admin:
        if not source.is_file():
            raise SystemExit('No existing database. Run initial installation first.')
        with sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True) as conn:
            table = conn.execute("SELECT name FROM sqlite_master WHERE name='user'").fetchone()
            if not table or not conn.execute("SELECT 1 FROM user WHERE username=? AND role='ADMIN'", (args.reset_admin,)).fetchone():
                raise SystemExit('No administrator with that username exists; no account was changed.')
    if source.is_file() and not args.reset_admin:
        with sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True) as conn:
            exists = conn.execute("SELECT name FROM sqlite_master WHERE name='user'").fetchone()
            if exists and conn.execute('SELECT 1 FROM user LIMIT 1').fetchone():
                print('Existing accounts preserved. No password was generated or reset.')
                return
    # Never emit a credential into redirected output, docker logs or journald.
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise SystemExit('An interactive terminal is required; do not redirect or record password output.')
    password = secrets.token_urlsafe(24)
    from app import create_app, startup_guard
    from app.models import User, db
    from app.security import audit
    application = create_app({'START_WORKER': False, 'ADMIN_PASSWORD': password})
    username = application.config['ADMIN_USERNAME']
    with application.app_context(), startup_guard(application):
        if args.reset_admin:
            user = User.query.filter_by(username=args.reset_admin, role='ADMIN').first()
            if not user:
                raise SystemExit('No administrator with that username exists; no account was changed.')
            user.set_password(password)
            user.must_change_password = True
            user.disabled_at = None
            username = user.username
        audit('Administrator password provisioned', username, actor_id=User.query.filter_by(username=username).one().id)
        db.session.commit()
        db.session.remove()
        db.engine.dispose()
    print('\nNarsika administrator: ' + username)
    print('Temporary password (shown once): ' + password)
    print('Copy it now. First sign-in requires a new password and confirmation.\n')


if __name__ == '__main__':
    main()
