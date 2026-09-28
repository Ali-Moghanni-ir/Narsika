#!/usr/bin/env python3
"""Install checksum-locked Galaxy artifacts into one release, without API resolution."""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.ansible_environment import ROOT, collection_root, environment, executable
from tools.patch_ansible import patch


class CollectionInstallError(RuntimeError):
    pass


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def load_lock(root=ROOT):
    import yaml
    from packaging.specifiers import SpecifierSet
    lock = json.loads((root / 'Playbooks/collections.lock.json').read_text())
    requirements = yaml.safe_load((root / 'Playbooks/requirements.yml').read_text())['collections']
    pins = {item['name']: str(item['version']) for item in lock['collections']}
    if lock['format'] != 1 or len(pins) != len(lock['collections']) or pins != {
            item['name']: str(item['version']) for item in requirements}:
        raise ValueError('Collection requirements and artifact lock disagree.')
    for item in lock['collections']:
        for name, constraint in item['dependencies'].items():
            if name not in pins or pins[name] not in SpecifierSet(constraint):
                raise ValueError('Collection lock has a missing or incompatible dependency: ' + name)
    return lock


def check_runtime(lock):
    core = importlib.metadata.version('ansible-core')
    minor = f'{sys.version_info.major}.{sys.version_info.minor}'
    if minor not in lock['python_minors'] or core != lock['ansible_core']:
        raise ValueError(f'Unsupported Ansible runtime: Python {minor}, core {core}. '
                         f'Requires Python {", ".join(lock["python_minors"])} and core {lock["ansible_core"]}.')
    if not executable('ansible-galaxy'):
        raise ValueError('ansible-galaxy is missing from this Python environment.')


def transient(error):
    if isinstance(error, urllib.error.HTTPError):
        return error.code in (408, 429, 500, 502, 503, 504)
    if isinstance(error, urllib.error.URLError):
        return transient(error.reason)
    return isinstance(error, (TimeoutError, ConnectionError, socket.gaierror)) and not isinstance(error, ssl.SSLError)


def obtain(item, destination, log, artifact_dir=None, attempts=3):
    """An optional read-only artifact directory enables checksum-verified offline installs."""
    filename = item['name'].replace('.', '-') + '-' + item['version'] + '.tar.gz'
    target = destination / filename
    if artifact_dir is not None:
        shutil.copyfile(Path(artifact_dir) / filename, target)
    else:
        if not item['url'].startswith('https://galaxy.ansible.com/api/'):
            raise ValueError('Unexpected artifact source in the collection lock.')
        for attempt in range(1, attempts + 1):
            try:
                print(f'Downloading {item["name"]} {item["version"]} ({attempt}/{attempts})', flush=True)
                start = time.monotonic()
                with urllib.request.urlopen(item['url'], timeout=45) as response, target.open('wb') as out:
                    size = 0
                    while chunk := response.read(1024 * 1024):
                        size += len(chunk)
                        if size > 64 * 1024 * 1024 or time.monotonic() - start > 180:
                            raise ValueError('Collection download exceeded its size or time limit.')
                        out.write(chunk)
                break
            except Exception as error:
                traceback.print_exc(file=log); log.flush()
                if attempt == attempts or not transient(error):
                    raise
                print('Transient Galaxy download failure; retrying the same pinned artifact.', flush=True)
                time.sleep(attempt * 2)
    if digest(target) != item['sha256']:
        raise ValueError('Artifact checksum mismatch; possible incomplete, modified or intercepted download.')
    return target


def verify(collections, lock):
    """Check exact installed versions, transitive closure and supported core metadata."""
    import yaml
    from packaging.specifiers import SpecifierSet
    for item in lock['collections']:
        path = Path(collections) / 'ansible_collections' / item['name'].replace('.', '/')
        info = json.loads((path / 'MANIFEST.json').read_text())['collection_info']
        if (info['namespace'] + '.' + info['name'], info['version'], info['dependencies']) != (
                item['name'], item['version'], item['dependencies']):
            raise ValueError('Installed collection differs from lock: ' + item['name'])
        metadata = yaml.safe_load((path / 'meta/runtime.yml').read_text())
        if lock['ansible_core'] not in SpecifierSet(metadata['requires_ansible']):
            raise ValueError('Collection does not support the pinned core: ' + item['name'])


def preflight(collections, lock, *, config, state, log):
    env = environment(collections=collections, config=config, state=state)
    result = subprocess.run([executable('ansible-config'), 'dump', '--format', 'json'],
                            env=env, capture_output=True, text=True, timeout=30)
    log.write(result.stdout + result.stderr); log.flush()
    if result.returncode:
        raise ValueError('Ansible configuration preflight failed.')
    values = {item['name']: item['value'] for item in json.loads(result.stdout) if 'name' in item}
    if values['COLLECTIONS_PATHS'] != [str(Path(collections).resolve())] or values['COLLECTIONS_SCAN_SYS_PATH']:
        raise ValueError('Ansible did not load the isolated collection root.')
    for args in [('connection', 'ansible.netcommon.network_cli'), ('module', 'cisco.ios.ios_command', 'community.routeros.command')]:
        result = subprocess.run([executable('ansible-doc'), '--json', '-t', args[0], *args[1:]],
                                env=env, capture_output=True, text=True, timeout=45)
        log.write(result.stderr); log.flush()
        if result.returncode or not set(args[1:]).issubset(json.loads(result.stdout or '{}')):
            raise ValueError('Ansible could not load the locked vendor/connection plugins.')


def install(collections=None, artifact_dir=None, root=ROOT):
    collections = Path(collections or collection_root()).absolute()
    # Never overwrite another environment, an activated release, or a partial old install.
    if collections.exists():
        raise CollectionInstallError('Collection destination already exists. Use a new release or an empty destination; nothing was overwritten.')
    collections.parent.mkdir(parents=True, exist_ok=True)
    log_path = collections.parent / 'ansible-install.log'
    fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.chmod(log_path, 0o600)
    current = 'preflight'
    with os.fdopen(fd, 'a') as log:
        try:
            lock = load_lock(root); check_runtime(lock)
            log.write(f'Python: {sys.version}\nAnsible Core: {lock["ansible_core"]}\n'); log.flush()
            with tempfile.TemporaryDirectory(prefix='narsika-collections-', dir=collections.parent) as directory:
                work = Path(directory); artifacts = work / 'artifacts'; artifacts.mkdir()
                stage = work / 'collections'
                paths = []
                for item in lock['collections']:
                    current = item['name']
                    paths.append(obtain(item, artifacts, log, artifact_dir))
                current = 'offline collection installation'
                import yaml
                requirements = work / 'requirements.yml'
                requirements.write_text(yaml.safe_dump({'collections': [
                    {'name': str(path), 'type': 'file'} for path in paths]}))
                env = environment(collections=stage, config=root / 'ansible.cfg', state=work / 'state')
                command = [executable('ansible-galaxy'), 'collection', 'install', '--offline',
                           '--no-cache', '--no-deps', '-vvv', '-r', str(requirements), '-p', str(stage)]
                # Offline install is deterministic: no retries, no Galaxy API version/cache resolver.
                result = subprocess.run(command, env=env, cwd=root, stdout=log,
                                        stderr=subprocess.STDOUT, timeout=180)
                log.flush()
                if result.returncode:
                    raise ValueError(f'Offline ansible-galaxy failed with exit {result.returncode}.')
                verify(stage, lock)
                try:
                    patch(stage)
                except SystemExit as error:
                    raise ValueError(str(error)) from error
                preflight(stage, lock, config=root / 'ansible.cfg', state=work / 'state', log=log)
                (stage / 'narsika-install.json').write_text(json.dumps({
                    'python': sys.version.split()[0], 'ansible_core': lock['ansible_core'],
                    'lock_sha256': digest(root / 'Playbooks/collections.lock.json'),
                    'artifacts': {item['name']: item['sha256'] for item in lock['collections']},
                }, indent=2) + '\n')
                # TemporaryDirectory is private. Collections must be readable by the service account.
                for path in stage.rglob('*'):
                    if not path.is_symlink():
                        path.chmod(0o755 if path.is_dir() or path.stat().st_mode & 0o111 else 0o644)
                stage.chmod(0o755)
                os.replace(stage, collections)
                print(f'Installed and verified {len(paths)} pinned collections in {collections}', flush=True)
                return collections
        except Exception as error:
            traceback.print_exc(file=log); log.flush()
            try: core = importlib.metadata.version('ansible-core')
            except importlib.metadata.PackageNotFoundError: core = 'not installed'
            if transient(error): cause = 'Galaxy artifact download failed due to a network/DNS/server error.'
            elif isinstance(error, urllib.error.HTTPError): cause = f'Galaxy artifact server returned HTTP {error.code}.'
            elif isinstance(error, (ssl.SSLError, urllib.error.URLError)): cause = 'HTTPS connection or certificate verification failed.'
            elif isinstance(error, FileNotFoundError): cause = 'A required local artifact or installation file is missing.'
            else: cause = str(error)
            raise CollectionInstallError(f'Failed to install Ansible collections.\nCollection/stage: {current}\n'
                f'Source: galaxy.ansible.com{ " (verified local artifacts)" if artifact_dir else ""}\n'
                f'Ansible Core: {core}\nPython: {sys.version.split()[0]}\n'
                f'Possible cause: {cause}\nDebug log: {log_path}\nThe active release was not changed.') from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--collections-path', type=Path)
    parser.add_argument('--artifact-dir', type=Path, default=os.environ.get('NARSIKA_COLLECTION_ARTIFACT_DIR'),
                        help='Read-only directory containing the locked .tar.gz artifacts; no downloads.')
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    if args.verify_only:
        lock = load_lock(); check_runtime(lock); verify(args.collections_path or collection_root(), lock)
        with tempfile.TemporaryDirectory(prefix='narsika-ansible-check-') as directory:
            with tempfile.TemporaryFile(mode='w+') as log:
                preflight(args.collections_path or collection_root(), lock, config=ROOT / 'ansible.cfg', state=Path(directory), log=log)
        print('PASS pinned runtime, collection metadata, isolated configuration and vendor/connection plugin loading')
    else:
        install(args.collections_path, args.artifact_dir)


if __name__ == '__main__':
    try: main()
    except (CollectionInstallError, ValueError, OSError) as error:
        print(str(error), file=sys.stderr); raise SystemExit(1)
