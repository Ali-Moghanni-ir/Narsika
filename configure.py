#!/usr/bin/env python3
"""Create private installation secrets. Uses only the Python standard library."""
import base64
import os
from pathlib import Path
import re
import secrets
import sys
import argparse
import tempfile
import ipaddress


def check_configuration(destination):
    """Validate configuration without importing third-party packages or exposing secrets."""
    if not destination.is_file():raise SystemExit('Missing .env. Run the installer to create it.')
    values={}
    for number,line in enumerate(destination.read_text(encoding='utf-8-sig').splitlines(),1):
        if not line.strip() or line.lstrip().startswith('#'):continue
        key,separator,value=line.partition('=')
        if not separator or not key.startswith('NARSIKA_') or key in values:
            raise SystemExit(f'Invalid or duplicate configuration entry at line {number}.')
        values[key]=value
    def invalid(key):raise SystemExit(f'Invalid {key} in .env. Correct it explicitly; existing keys and data were preserved.')
    if len(values.get('NARSIKA_SECRET_KEY',''))<32:invalid('NARSIKA_SECRET_KEY')
    try:
        key=values.get('NARSIKA_ENCRYPTION_KEY','').encode('ascii')
        if len(base64.b64decode(key,altchars=b'-_',validate=True))!=32:invalid('NARSIKA_ENCRYPTION_KEY')
    except (ValueError,UnicodeError):invalid('NARSIKA_ENCRYPTION_KEY')
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}',values.get('NARSIKA_ADMIN_USERNAME','admin')):invalid('NARSIKA_ADMIN_USERNAME')
    if 'NARSIKA_ADMIN_PASSWORD_BASE64' in values:
        try:password=base64.b64decode(values['NARSIKA_ADMIN_PASSWORD_BASE64'],validate=True).decode()
        except (ValueError,UnicodeError):invalid('NARSIKA_ADMIN_PASSWORD_BASE64')
        if not 12<=len(password)<=256 or any(c in password for c in '\r\n\0'):invalid('NARSIKA_ADMIN_PASSWORD_BASE64')
    for key,default,minimum,maximum in [('NARSIKA_PORT','8000',1,65535),('NARSIKA_SCAN_MAX_HOSTS','256',1,256),('NARSIKA_JOB_TIMEOUT','300',5,300),('NARSIKA_TRUST_PROXY_HOPS','0',0,1)]:
        try:valid=minimum<=int(values.get(key,default))<=maximum
        except ValueError:valid=False
        if not valid:invalid(key)
    for key in ('NARSIKA_COOKIE_SECURE','NARSIKA_START_WORKER'):
        if key in values and values[key].lower() not in ('true','false'):invalid(key)
    try:
        for network in values.get('NARSIKA_ALLOWED_NETWORKS','10.0.0.0/8').split(','):ipaddress.IPv4Network(network.strip())
        for network in values.get('NARSIKA_WEB_NETWORKS','127.0.0.0/8').split(','):ipaddress.IPv4Network(network.strip())
        ipaddress.ip_address(values.get('NARSIKA_BIND_ADDRESS','127.0.0.1'))
    except ValueError:raise SystemExit('Invalid allowed network or bind address in .env.')
    print('Configuration is valid. Existing installation keys were preserved.')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--clear-bootstrap',action='store_true',help='Remove the initial admin password from existing configuration after successful first login.')
    parser.add_argument('--check',action='store_true',help='Validate existing configuration without modifying it.')
    parser.add_argument('--destination',type=Path)
    parser.add_argument('--web-networks',default='127.0.0.0/8,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16')
    parser.add_argument('--port',type=int,default=int(os.getenv('NARSIKA_PORT','8000')))
    args=parser.parse_args()
    destination=args.destination or Path(os.getenv('NARSIKA_ENV_FILE', str(Path(__file__).resolve().parent/'.env')))
    if args.check:
        check_configuration(destination)
        return
    if args.clear_bootstrap:
        if not destination.is_file():raise SystemExit('No installation configuration exists.')
        lines=destination.read_text().splitlines()
        content='\n'.join(line for line in lines if line.partition('=')[0] not in ('NARSIKA_ADMIN_PASSWORD','NARSIKA_ADMIN_PASSWORD_BASE64'))+'\n'
        previous=destination.stat()
        fd,temporary=tempfile.mkstemp(dir=destination.parent,prefix='.narsika-config-')
        os.fchmod(fd,previous.st_mode & 0o777)
        if hasattr(os,'fchown'):os.fchown(fd,previous.st_uid,previous.st_gid)
        with os.fdopen(fd,'w') as stream:
            stream.write(content);stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,destination)
        print('Bootstrap password removed. Recreate the container to remove its old environment. Preserve both encryption/session keys.')
        return
    if destination.exists():
        print('.env already exists. Keep the original encryption key; edit configuration explicitly if needed.')
        return
    username=os.getenv('NARSIKA_ADMIN_USERNAME','admin')
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}',username):raise SystemExit('Invalid NARSIKA_ADMIN_USERNAME.')
    if not 1024<=args.port<=65535:raise SystemExit('Use an unprivileged HTTP port, 1024–65535.')
    for value in args.web_networks.split(','):ipaddress.IPv4Network(value.strip(),strict=True)
    content='\n'.join([
        '# Keep this file private and back it up separately from application data.',
        'NARSIKA_SECRET_KEY='+secrets.token_urlsafe(48),
        'NARSIKA_ENCRYPTION_KEY='+base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
        'NARSIKA_ADMIN_USERNAME='+username,
        'NARSIKA_ALLOWED_NETWORKS=10.0.0.0/8,172.16.0.0/12,192.168.0.0/16',
        'NARSIKA_COOKIE_SECURE=false',
        'NARSIKA_TRUST_PROXY_HOPS=0',
        'NARSIKA_BIND_ADDRESS=0.0.0.0',
        'NARSIKA_WEB_NETWORKS='+args.web_networks,
        'NARSIKA_PORT='+str(args.port),
        'NARSIKA_SCAN_MAX_HOSTS=256',
        'NARSIKA_JOB_TIMEOUT=300',''])
    fd=os.open(destination,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as stream:stream.write(content)
    print('Private keys created. The installer will generate an initial password directly in the database.')

if __name__=='__main__':main()
