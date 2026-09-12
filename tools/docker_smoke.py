#!/usr/bin/env python3
"""Build the actual image, test authenticated HTTP and verify named-volume persistence."""
import argparse
import base64
import http.cookiejar
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from terminal_test import run_terminal
ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image',default='narsika:ci')
    parser.add_argument('--skip-build',action='store_true')
    args=parser.parse_args()
    subprocess.run(['docker','info'],check=True,stdout=subprocess.DEVNULL)
    if not args.skip_build:subprocess.run(['docker','build','-t',args.image,str(ROOT)],check=True,timeout=1200)
    name='narsika-test-'+uuid.uuid4().hex[:12];volume=name+'-data'
    changed=secrets.token_urlsafe(24)
    env={**os.environ,'NARSIKA_SECRET_KEY':secrets.token_urlsafe(48),'NARSIKA_ENCRYPTION_KEY':base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()}
    keys=sum((['-e',k] for k in ('NARSIKA_SECRET_KEY','NARSIKA_ENCRYPTION_KEY')),[])
    volume_created=False;container_created=False
    try:
        subprocess.run(['docker','volume','create',volume],check=True,stdout=subprocess.DEVNULL);volume_created=True
        output=run_terminal(['docker','run','--rm','-it','--log-driver=none','--network=none',
                             '-v',volume+':/var/lib/narsika',*keys,args.image,'python','tools/bootstrap.py'],env=env)
        password=re.search(r'Temporary password \(shown once\): ([A-Za-z0-9_-]+)',output).group(1)
        subprocess.run(['docker','run','-d','--name',name,'--init','--cap-drop=ALL','--cap-add=NET_RAW','--security-opt=no-new-privileges','-p','127.0.0.1::8000','-v',volume+':/var/lib/narsika',*keys,args.image],env=env,check=True,stdout=subprocess.DEVNULL);container_created=True
        binding=subprocess.check_output(['docker','port',name,'8000/tcp'],text=True).strip()
        base='http://'+binding
        def wait_ready():
            deadline=time.monotonic()+60
            while time.monotonic()<deadline:
                try:
                    with urllib.request.urlopen(base+'/healthz',timeout=3) as response:
                        if response.status==200:return
                except (OSError,urllib.error.URLError):pass
                time.sleep(.5)
            raise RuntimeError('Container did not become healthy.')
        wait_ready()
        def sign_in(value):
            opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
            html=opener.open(base+'/login').read().decode()
            csrf=re.search(r'name="csrf-token" content="([^"]+)"',html).group(1)
            def call(path,body=None):
                request=urllib.request.Request(base+path,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json','X-CSRFToken':csrf})
                return json.loads(opener.open(request,timeout=20).read())
            call('/login',{'username':'admin','password':value})
            html=opener.open(base+'/change-password' if value==password else base+'/').read().decode()
            csrf=re.search(r'name="csrf-token" content="([^"]+)"',html).group(1)
            return call
        call=sign_in(password)
        call('/change-password',{'current':password,'password':changed,'confirm':changed})
        assert call('/api/devices')['data']['items']==[]
        created=call('/api/devices',{'name':'Container persistence check','ip_address':'10.255.255.1','platform':'cisco'})['data']
        assert created['health'] is None
        assert subprocess.check_output(['docker','exec',name,'id','-u'],text=True).strip()=='10001'
        subprocess.run(['docker','restart',name],check=True,stdout=subprocess.DEVNULL)
        # Docker may assign a different ephemeral host port when restarting.
        binding=subprocess.check_output(['docker','port',name,'8000/tcp'],text=True).strip()
        base='http://'+binding
        wait_ready();call=sign_in(changed)
        assert [d['id'] for d in call('/api/devices')['data']['items']]==[created['id']]
        assert call('/api/system')['data']['worker_available'] is True
        print('PASS image build, non-root HTTP authentication, password change, real database writes and restart persistence')
    except Exception:
        if container_created:
            subprocess.run(['docker','inspect','--format','{{.State.Status}} exit={{.State.ExitCode}}',name],check=False)
            subprocess.run(['docker','logs','--tail','100',name],check=False)
        raise
    finally:
        if container_created:subprocess.run(['docker','rm','-f',name],check=False,stdout=subprocess.DEVNULL)
        if volume_created:subprocess.run(['docker','volume','rm',volume],check=False,stdout=subprocess.DEVNULL)

if __name__=='__main__':main()
