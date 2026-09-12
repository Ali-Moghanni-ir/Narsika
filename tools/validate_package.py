#!/usr/bin/env python3
"""Static package checks and Gunicorn configuration loading; no browser or HTTP listener."""
import ast
import base64
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import yaml
from package_release import release_files
from terminal_test import run_terminal
ROOT=Path(__file__).resolve().parents[1]

def main():
    files=release_files()
    for path in files:
        if path.suffix=='.py':ast.parse(path.read_text(),filename=str(path))
    print('PASS portable package paths and required release files')
    print('PASS Python syntax')
    for path in (ROOT/'app/static/js').glob('*.js'):subprocess.run(['node','--check',str(path)],check=True,capture_output=True)
    print('PASS JavaScript syntax (11 modules)')
    for path in [ROOT/'run_linux.sh',ROOT/'tools/run_native_linux.sh',ROOT/'tools/native_platform.sh',ROOT/'tools/run_docker_linux.sh']:subprocess.run(['bash','-n',str(path)],check=True)
    print('PASS Linux launcher syntax')
    compose=yaml.safe_load((ROOT/'docker-compose.yml').read_text())
    assert compose['services']['narsika']['volumes']==['narsika-data:/var/lib/narsika']
    assert compose['services']['narsika']['build']=='.'
    print('PASS Compose YAML structure and persistent volume mapping')
    with tempfile.TemporaryDirectory() as directory:
        path=Path(directory);shutil.copy(ROOT/'configure.py',path/'configure.py')
        env={**os.environ,'NARSIKA_ADMIN_PASSWORD':'package-test-password-with-$-quotes-\'','NARSIKA_ADMIN_USERNAME':'test-admin'}
        result=subprocess.run([sys.executable,str(path/'configure.py')],env=env,capture_output=True,text=True,check=True)
        values=dict(line.split('=',1) for line in (path/'.env').read_text().splitlines() if line and not line.startswith('#'))
        assert 'NARSIKA_ADMIN_PASSWORD_BASE64' not in values
        assert 'NARSIKA_ADMIN_PASSWORD' not in values
        before=(path/'.env').read_bytes()
        subprocess.run([sys.executable,str(path/'configure.py')],env=env,check=True,capture_output=True)
        assert (path/'.env').read_bytes()==before
        subprocess.run([sys.executable,str(path/'configure.py'),'--clear-bootstrap'],env=env,check=True,capture_output=True)
        cleared=dict(line.split('=',1) for line in (path/'.env').read_text().splitlines() if line and not line.startswith('#'))
        assert 'NARSIKA_ADMIN_PASSWORD_BASE64' not in cleared
        assert cleared['NARSIKA_ENCRYPTION_KEY']==values['NARSIKA_ENCRYPTION_KEY']
        print('PASS configuration generation without passwords and non-overwrite')
        env.update(values,NARSIKA_DATA_DIR=str(path/'data'),NARSIKA_BACKUP_DIR=str(path/'data/backups'))
        run_terminal([sys.executable,str(ROOT/'tools/bootstrap.py')],env=env)
        result=subprocess.run([sys.executable,'-m','gunicorn','--check-config','--config',str(ROOT/'gunicorn.conf.py'),'wsgi:app'],cwd=ROOT,env=env,capture_output=True,text=True,timeout=20)
        if result.returncode:raise RuntimeError('Gunicorn configuration failed: '+result.stderr)
        print('PASS Gunicorn configuration and real application factory load')
    print('Docker image build: NOT RUN' if not shutil.which('docker') else 'Docker is available; use docker compose build for image validation.')

if __name__=='__main__':main()
