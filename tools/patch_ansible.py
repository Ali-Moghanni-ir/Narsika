#!/usr/bin/env python3
"""Apply the pinned netcommon known-hosts compatibility fix to a specified collection directory."""
import argparse
import hashlib
from pathlib import Path

EXPECTED='dfebb86384097a8072c0625b546de02f1b39b63f9c0dbd563ed51d819de5881b'
MARKER='# Narsika: explicit application-owned known-hosts file.'

def patch(collections):
    target=Path(collections)/'ansible_collections/ansible/netcommon/plugins/connection/libssh.py'
    raw=target.read_bytes();source=raw.decode()
    if MARKER in source:
        print('Narsika known-hosts patch is already present.');return
    if hashlib.sha256(raw).hexdigest()!=EXPECTED:
        raise SystemExit('Unexpected netcommon libssh source. Review the compatibility patch before changing collection versions.')
    old='        ssh_connect_kwargs = {}\n'
    addition='''        ssh_connect_kwargs = {}
        # Narsika: explicit application-owned known-hosts file.
        known_hosts = os.environ.get("NARSIKA_ANSIBLE_KNOWN_HOSTS")
        if known_hosts:
            if not os.path.isfile(known_hosts):
                raise AnsibleConnectionFailure("Narsika known-hosts file is unavailable")
            ssh_connect_kwargs["knownhosts"] = known_hosts
'''
    if source.count(old)!=1:raise SystemExit('Unexpected patch anchor count.')
    target.write_text(source.replace(old,addition))
    print('Applied Narsika known-hosts compatibility patch to ansible.netcommon 8.6.2.')

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('collections_directory')
    patch(parser.parse_args().collections_directory)
