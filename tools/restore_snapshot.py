#!/usr/bin/env python3
"""Decrypt a migration snapshot into a new offline SQLite database; never overwrite."""
import argparse
import os
from pathlib import Path
import sqlite3
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
    if not raw.startswith(b'SQLite format 3\x00'):raise SystemExit('Snapshot is not a SQLite database.')
    fd=os.open(args.destination,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'wb') as stream:stream.write(raw)
    with sqlite3.connect(args.destination) as connection:
        if connection.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise SystemExit('Restored database failed integrity check.')
    print('Restored a private offline database. It may contain historical plaintext secrets; protect it accordingly.')

if __name__=='__main__':main()
