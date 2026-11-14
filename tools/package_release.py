#!/usr/bin/env python3
"""Build a release from explicit source roots; exclude private/runtime/generated files."""
import argparse
import hashlib
from pathlib import Path
import zipfile
ROOT=Path(__file__).resolve().parents[1]
ROOT_FILES={'.gitattributes','.dockerignore','.env.example','.gitignore','CHANGELOG.md','CONTRIBUTING.md','Dockerfile','LICENSE','README.md','SECURITY.md','app.py','ansible.cfg','configure.py','constraints.txt','docker-compose.yml','gunicorn.conf.py','inventory.ini','pytest.ini','requirements-dev.txt','requirements.txt','run_linux.sh','run_windows.bat','runtime.py','wsgi.py'}
DIRECTORIES={'app','Playbooks','callback_plugins','docs','tools','tests','deploy','.github'}


def release_files(root=ROOT):
    paths=[root/name for name in ROOT_FILES]
    for name in DIRECTORIES:
        paths.extend(p for p in (root/name).rglob('*') if p.is_file() and not p.is_symlink() and '__pycache__' not in p.parts and p.suffix not in ('.pyc','.pyo') and p.name!='connect_frontend.py')
    paths=sorted(paths,key=lambda p:p.relative_to(root).as_posix())
    seen={}
    for path in paths:
        if not path.is_file():raise ValueError('Required release file is missing: '+str(path))
        relative=path.relative_to(root)
        for part in [relative,*relative.parents]:
            key=part.as_posix().casefold()
            if key in seen and seen[key]!=part.as_posix():raise ValueError('Case-insensitive path collision: '+str(part))
            seen[key]=part.as_posix()
    return paths


def check_archive(path: Path) -> None:
    """Fail unless every shipped file is listed in the manifest with its checksum, and nothing else is."""
    with zipfile.ZipFile(path) as archive:
        if archive.testzip():raise ValueError('ZIP integrity check failed.')
        listed=set()
        for line in archive.read('narsika/MANIFEST.sha256').decode().splitlines():
            expected,name=line.split('  ',1)
            listed.add('narsika/'+name)
            try:
                content=archive.read('narsika/'+name)
            except KeyError:
                raise ValueError('The manifest lists a file the archive does not contain: '+name) from None
            if hashlib.sha256(content).hexdigest()!=expected:raise ValueError('Manifest mismatch: '+name)
        shipped={name for name in archive.namelist() if not name.endswith('/')}-{'narsika/MANIFEST.sha256'}
        unlisted=sorted(shipped-listed)
        if unlisted:raise ValueError('Shipped files are missing from MANIFEST.sha256: '+', '.join(unlisted))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination',type=Path)
    args=parser.parse_args()
    files=release_files()
    manifest=ROOT/'MANIFEST.sha256'
    # Bytes, not write_text: on Windows text mode would write CRLF and break `sha256sum -c`.
    manifest.write_bytes(''.join(hashlib.sha256(path.read_bytes()).hexdigest()+'  '+path.relative_to(ROOT).as_posix()+'\n' for path in files).encode('ascii'))
    args.destination.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(args.destination,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
        for path in files+[manifest]:archive.write(path,'narsika/'+path.relative_to(ROOT).as_posix())
    check_archive(args.destination)
    print(f'PASS {len(files)+1} files; SHA256 {hashlib.sha256(args.destination.read_bytes()).hexdigest()}')

if __name__=='__main__':main()
