#!/usr/bin/env python3
"""Test real Gunicorn HTTP and restart persistence in a disposable data directory."""
import base64
import http.cookiejar
import json
import os
from pathlib import Path
import re
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from terminal_test import run_terminal

ROOT=Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory(prefix='narsika-http-') as directory:
        data=Path(directory)
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0))
            port=listener.getsockname()[1]
        env={**os.environ,'NARSIKA_DATA_DIR':directory,'NARSIKA_BACKUP_DIR':str(data/'backups'),
             'NARSIKA_ENV_FILE':str(data/'absent.env'),'NARSIKA_SECRET_KEY':secrets.token_urlsafe(48),
             'NARSIKA_ENCRYPTION_KEY':base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
             'NARSIKA_HTTP_BIND':f'127.0.0.1:{port}','NARSIKA_WEB_NETWORKS':'127.0.0.0/8',
             'NARSIKA_START_WORKER':'true','PYTHONDONTWRITEBYTECODE':'1'}
        output=run_terminal([sys.executable,str(ROOT/'tools/bootstrap.py')],env=env)
        password=re.search(r'Temporary password \(shown once\): ([A-Za-z0-9_-]+)',output).group(1)
        changed=secrets.token_urlsafe(24)
        base=f'http://127.0.0.1:{port}'
        process=None
        with (data/'server.log').open('wb') as log:
            def start():
                child=subprocess.Popen([sys.executable,str(ROOT/'app.py')],cwd=ROOT,env=env,
                                       stdout=log,stderr=log,start_new_session=True)
                opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
                for _ in range(60):
                    if child.poll() is not None:raise RuntimeError('Gunicorn exited during startup.')
                    try:
                        with opener.open(base+'/healthz',timeout=1) as result:
                            if result.status==200:return child
                    except OSError:time.sleep(.2)
                child.terminate();child.wait(timeout=10)
                raise RuntimeError('Gunicorn did not become ready.')
            def stop(child):
                os.killpg(child.pid,signal.SIGTERM)
                child.wait(timeout=15)
            def login(value,forced):
                opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),
                    urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
                html=opener.open(base+'/login').read().decode()
                csrf=re.search(r'name="csrf-token" content="([^"]+)"',html).group(1)
                def call(path,body=None):
                    request=urllib.request.Request(base+path,
                        data=json.dumps(body).encode() if body is not None else None,
                        headers={'Content-Type':'application/json','X-CSRFToken':csrf})
                    with opener.open(request,timeout=15) as response:return json.loads(response.read())
                result=call('/login',{'username':'admin','password':value})
                assert result['data']['redirect']==('/change-password' if forced else '/')
                html=opener.open(base+result['data']['redirect']).read().decode()
                csrf=re.search(r'name="csrf-token" content="([^"]+)"',html).group(1)
                return opener,call
            try:
                process=start()
                opener,call=login(password,True)
                call('/change-password',{'current':password,'password':changed,'confirm':changed})
                assert call('/api/devices')['data']['items']==[]
                assert call('/api/system')['data']['worker_available'] is True
                for page in ('index','settings','playbooks','health','interfaces','vlan','acl','discovery','audit','backups'):
                    assert opener.open(base+'/'+page+'.html').status==200
                assert opener.open(base+'/static/img/narsika-relay.png').status==200
                item=call('/api/devices',{'name':'HTTP test record','ip_address':'10.255.254.253','platform':'cisco'})['data']
                assert item['health'] is None
                stop(process);process=None
                process=start()
                opener,call=login(changed,False)
                assert [row['id'] for row in call('/api/devices')['data']['items']]==[item['id']]
                stop(process);process=None
            finally:
                if process is not None and process.poll() is None:stop(process)
        logdata=(data/'server.log').read_bytes()
        assert password.encode() not in logdata and changed.encode() not in logdata
        print('PASS live Gunicorn HTTP, random bootstrap, forced change, 10 product pages, logo, empty inventory, persistent write/restart and password-free logs')


if __name__=='__main__':main()
