#!/usr/bin/env python3
"""Create a consistent SQLite snapshot for an installation backup; never overwrites."""
import argparse
import os
import sqlite3
from pathlib import Path

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path)
    parser.add_argument('destination',type=Path)
    args=parser.parse_args()
    if not args.source.is_file():raise SystemExit('Database does not exist.')
    fd=os.open(args.destination,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);os.close(fd)
    with sqlite3.connect(args.source.resolve().as_uri()+'?mode=ro',uri=True) as src,sqlite3.connect(args.destination) as dst:src.backup(dst)
    print('SQLite snapshot created. Also preserve encrypted backups, uploads, known_hosts and the original encryption key.')

if __name__=='__main__':main()
