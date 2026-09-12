#!/usr/bin/env python3
"""Ansible parser validation only; this command never connects to a device."""
import concurrent.futures
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
ROOT=Path(__file__).resolve().parents[1]

def main():
    executable=shutil.which('ansible-playbook')
    if not executable:raise SystemExit('Install requirements and Ansible collections first.')
    paths=sorted((ROOT/'Playbooks/Cisco').glob('*.yml'))+sorted((ROOT/'Playbooks/MikroTik').glob('*.yml'))+sorted((ROOT/'Playbooks/Original').glob('*.yml'))
    with tempfile.TemporaryDirectory() as directory:
        inventory=Path(directory)/'inventory.yml'
        inventory.write_text('all:\n  children:\n    cisco:\n      hosts:\n        cisco_test:\n          ansible_host: 192.0.2.1\n          ansible_connection: ansible.netcommon.network_cli\n          ansible_network_os: cisco.ios.ios\n    mikrotik:\n      hosts:\n        mikrotik_test:\n          ansible_host: 192.0.2.2\n          ansible_connection: ansible.netcommon.network_cli\n          ansible_network_os: community.routeros.routeros\n    cisco_routers:\n      hosts:\n        cisco_test: {}\n')
        def check(path):
            result=subprocess.run([executable,'--syntax-check','-i',str(inventory),str(path)],capture_output=True,text=True,timeout=60,cwd=directory)
            return (str(path.relative_to(ROOT)),result.returncode,result.stderr.strip())
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(check,paths))
    for name,code,error in results:
        print(('PASS' if code==0 else 'FAIL')+' '+name)
        if code:print(error)
    raise SystemExit(any(code for _,code,_ in results))

if __name__=='__main__':main()
