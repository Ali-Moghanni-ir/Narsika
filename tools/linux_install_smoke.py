#!/usr/bin/env python3
"""CI-only: exercise the release installer twice in an isolated project and volume."""
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import tempfile
import uuid
from package_release import release_files, ROOT
from terminal_test import run_terminal


def main():
    with tempfile.TemporaryDirectory(prefix='narsika install ') as directory:
        project=Path(directory)
        for source in release_files():
            destination=project/source.relative_to(ROOT)
            destination.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(source,destination)
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
        name='narsika-install-'+uuid.uuid4().hex[:12]
        env={**os.environ,'COMPOSE_PROJECT_NAME':name,'NARSIKA_PORT':str(port)}
        try:
            run_terminal(['bash','run_linux.sh','--docker'],cwd=project,env=env,timeout=1200)
            before=(project/'.env').read_bytes()
            assert (project/'.env').stat().st_mode & 0o077 == 0
            # Re-running must not prompt, change keys or replace the administrator password.
            run_terminal(['bash','run_linux.sh','--docker'],cwd=project,env=env,timeout=180)
            assert (project/'.env').read_bytes()==before
            print('PASS optional Linux Docker installer, terminal-only provisioning, private configuration, health and repeat installation')
        finally:
            subprocess.run(['docker','compose','down','--volumes'],cwd=project,env=env,check=False)

if __name__=='__main__':main()
