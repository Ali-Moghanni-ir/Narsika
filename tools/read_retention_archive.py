#!/usr/bin/env python3
"""Verify and extract an encrypted retention archive into a new offline directory."""
import argparse
import base64
import gzip
import json
import os
from pathlib import Path
import sys
from cryptography.fernet import Fernet
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from runtime import load_environment


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path)
    parser.add_argument('destination',type=Path)
    args=parser.parse_args();load_environment()
    raw=Fernet(os.environ['NARSIKA_ENCRYPTION_KEY'].encode()).decrypt(args.source.read_bytes())
    archive=json.loads(gzip.decompress(raw))
    if archive.get('format')!=1:raise SystemExit('Unsupported retention archive format.')
    if any(Path(name).name!=name for name in archive['files']):raise SystemExit('Invalid archive filename.')
    args.destination.mkdir(mode=0o700,parents=False,exist_ok=False)
    rows=args.destination/'records.json'
    rows.write_text(json.dumps(archive['tables'],indent=2));rows.chmod(0o600)
    for name,value in archive['files'].items():
        path=args.destination/name;path.write_bytes(base64.b64decode(value,validate=True));path.chmod(0o600)
    print('Verified and extracted offline records and encrypted files. No live database was changed.')

if __name__=='__main__':main()
