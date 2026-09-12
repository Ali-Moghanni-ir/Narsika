"""Compatibility entry point. Starts Gunicorn with the supported runtime configuration."""
import os
import sys
from pathlib import Path
from runtime import load_environment

if __name__=='__main__':
    load_environment()
    if os.name!='posix':raise SystemExit('Use Docker Desktop or WSL2 on Windows.')
    os.chdir(Path(__file__).resolve().parent)
    # Validate and migrate before starting the single Gunicorn worker.
    from app import create_app
    from app.models import db
    application=create_app({'START_WORKER':False})
    with application.app_context():db.session.remove();db.engine.dispose()
    del application
    os.execv(sys.executable,[sys.executable,'-m','gunicorn','--config','gunicorn.conf.py','wsgi:app'])
