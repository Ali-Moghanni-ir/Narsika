#!/usr/bin/env python3
"""Copy a legacy SQLite database without modifying the source or overwriting a target."""
import argparse
import os
from pathlib import Path
import sqlite3
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from runtime import load_environment

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path)
    parser.add_argument('--data-dir',type=Path,default=None)
    args=parser.parse_args();load_environment()
    source=args.source.resolve()
    if not source.is_file():raise SystemExit('Source database does not exist.')
    destination=(args.data_dir or Path(os.getenv('NARSIKA_DATA_DIR',str(ROOT/'instance')))).resolve()
    destination.mkdir(parents=True,exist_ok=True,mode=0o700)
    target=destination/'narsika.db'
    fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);os.close(fd)
    try:
        with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True) as src,sqlite3.connect(target) as dst:
            tables={row[0] for row in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {'user','group','device'}<=tables:raise ValueError('The source does not have the expected legacy schema.')
            src.backup(dst)
        print('Copied legacy database. Keep the source and configure encryption keys before starting Narsika.')
    except Exception:
        print('Import failed. The reserved destination is retained for inspection; the source was not modified.',file=sys.stderr)
        raise

if __name__=='__main__':main()
