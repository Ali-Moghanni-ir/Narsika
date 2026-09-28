"""Shared, explicit Ansible environment for install, diagnostics and job workers."""
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
PREFIX = Path(sys.prefix).resolve()


def collection_root(root=ROOT, prefix=None, environ=None):
    root = Path(root).resolve()
    prefix = Path(prefix or PREFIX)
    environ = os.environ if environ is None else environ
    # A native release always owns its dependencies, even through current -> release.
    if (root / '.venv/bin/python').is_file():
        return root / '.venv/collections'
    value = environ.get('ANSIBLE_COLLECTIONS_PATH', str(prefix / 'collections'))
    if os.pathsep in value or not Path(value).is_absolute():
        raise ValueError('ANSIBLE_COLLECTIONS_PATH must be one absolute collection root.')
    return Path(value).resolve()


def executable(name):
    """Never select another release's Ansible from the invoking user's PATH."""
    if name not in ('ansible', 'ansible-playbook', 'ansible-galaxy', 'ansible-config', 'ansible-doc'):
        raise ValueError('Unsupported Ansible command.')
    path = PREFIX / 'bin' / name
    return str(path) if path.is_file() else None


def environment(*, collections=None, config=None, state=None, base=None):
    source = dict(os.environ if base is None else base)
    collections = Path(collections) if collections is not None else collection_root(environ=source)
    if not collections.is_absolute():
        raise ValueError('Collection root must be absolute.')
    # Inherited global Galaxy servers, module paths and plural legacy variables
    # must not redirect a Narsika subprocess to an unrelated Ansible installation.
    env = {k: v for k, v in source.items() if not k.startswith('ANSIBLE_')}
    env.update(ANSIBLE_CONFIG=str(Path(config or ROOT / 'ansible.cfg').resolve()),
               ANSIBLE_COLLECTIONS_PATH=str(collections.resolve()),
               ANSIBLE_COLLECTIONS_SCAN_SYS_PATH='False', ANSIBLE_NOCOLOR='1',
               PATH=str(PREFIX / 'bin') + os.pathsep + source.get('PATH', os.defpath))
    if state is not None:
        state = Path(state).resolve()
        env.update(ANSIBLE_HOME=str(state), ANSIBLE_GALAXY_CACHE_DIR=str(state / 'galaxy-cache'),
                   ANSIBLE_GALAXY_TOKEN_PATH=str(state / 'unused-token'),
                   ANSIBLE_LOCAL_TEMP=str(state / 'tmp'))
    return env
