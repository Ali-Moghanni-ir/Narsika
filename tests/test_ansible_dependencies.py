"""Ansible isolation and reproducibility regressions; no external network/device writes."""
import datetime
import io
import json
import hashlib
import importlib.util
import os
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import urllib.error

import pytest

from tools import ansible_environment as runtime
from tools import install_collections as installer


def test_broken_galaxy_response_cache_reproduces_results_crash_and_no_cache_avoids_it(tmp_path, monkeypatch):
    from ansible import constants
    from ansible.galaxy import api
    monkeypatch.setattr(constants, 'GALAXY_CACHE_DIR', str(tmp_path))
    url = 'https://galaxy.ansible.com/api/v3/collections/versions/'
    expires = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
    broken = {'version': 1, api.get_cache_id(url): {'/api/v3/collections/versions/': {
        'expires': expires, 'paginated': False}}}
    cached = api.GalaxyAPI(None, 'local-test', 'https://galaxy.ansible.com', no_cache=False)
    cached._cache = broken
    with pytest.raises(KeyError, match='results'):
        cached._call_galaxy(url, cache=True)
    # The same bad cache on disk is not consulted with the supported no-cache switch.
    cache = tmp_path / 'api.json'; cache.write_text(json.dumps(broken)); before = cache.read_bytes()
    fresh = api.GalaxyAPI(None, 'local-test', 'https://galaxy.ansible.com', no_cache=True)
    monkeypatch.setattr(api, 'open_url', lambda *a, **k: io.BytesIO(b'{"data": [], "links": {"next": null}}'))
    assert fresh._call_galaxy(url, cache=True)['data'] == []
    assert cache.read_bytes() == before


def test_native_release_root_ignores_inherited_global_collection_path(tmp_path):
    release = tmp_path / 'releases/one'
    python = release / '.venv/bin/python'; python.parent.mkdir(parents=True); python.touch()
    current = tmp_path / 'current'; current.symlink_to(release)
    assert runtime.collection_root(current, environ={'ANSIBLE_COLLECTIONS_PATH': '/root/.ansible/collections'}) == release / '.venv/collections'


@pytest.mark.parametrize('value', ['relative/path', '/root/a:/root/b', ''])
def test_collection_root_rejects_ambiguous_paths(tmp_path, value):
    with pytest.raises(ValueError):
        runtime.collection_root(tmp_path, environ={'ANSIBLE_COLLECTIONS_PATH': value})


def test_environment_removes_global_overrides_and_preserves_proxy_and_ca(tmp_path):
    env = runtime.environment(collections=tmp_path / 'collections', state=tmp_path / 'state', base={
        'ANSIBLE_COLLECTIONS_PATHS': '/root/global', 'ANSIBLE_LIBRARY': '/global/modules',
        'ANSIBLE_GALAXY_SERVER_LIST': 'unexpected', 'HTTPS_PROXY': 'https://proxy.example',
        'SSL_CERT_FILE': '/private/ca.pem', 'PATH': '/unrelated/bin'})
    assert env['ANSIBLE_COLLECTIONS_PATH'] == str(tmp_path / 'collections')
    assert env['ANSIBLE_COLLECTIONS_SCAN_SYS_PATH'] == 'False'
    assert not set(env) & {'ANSIBLE_COLLECTIONS_PATHS', 'ANSIBLE_LIBRARY', 'ANSIBLE_GALAXY_SERVER_LIST'}
    assert env['HTTPS_PROXY'] == 'https://proxy.example' and env['SSL_CERT_FILE'] == '/private/ca.pem'
    assert env['ANSIBLE_GALAXY_CACHE_DIR'].startswith(str(tmp_path))


def test_ansible_binary_is_from_current_python_not_path(tmp_path, monkeypatch):
    fake = tmp_path / 'ansible-playbook'; fake.write_text('unrelated'); fake.chmod(0o755)
    monkeypatch.setenv('PATH', str(tmp_path))
    assert runtime.executable('ansible-playbook') == str(Path(sys.prefix) / 'bin/ansible-playbook')


def test_running_worker_stays_on_old_release_after_current_switch(tmp_path, monkeypatch):
    releases = []
    for name in ('old', 'new'):
        release = tmp_path / name; (release / '.venv/bin').mkdir(parents=True)
        for binary in ('python', 'ansible-playbook'): (release / '.venv/bin' / binary).touch()
        (release / 'tools').mkdir()
        (release / 'tools/ansible_environment.py').write_bytes(Path(runtime.__file__).read_bytes())
        releases.append(release)
    current = tmp_path / 'current'; current.symlink_to(releases[0])
    monkeypatch.setattr(sys, 'prefix', str(current / '.venv'))
    spec = importlib.util.spec_from_file_location('release_fixture', current / 'tools/ansible_environment.py')
    worker = importlib.util.module_from_spec(spec); spec.loader.exec_module(worker)
    switch = tmp_path / 'switch'; switch.symlink_to(releases[1]); os.replace(switch, current)
    assert worker.executable('ansible-playbook') == str(releases[0] / '.venv/bin/ansible-playbook')
    assert worker.collection_root() == releases[0] / '.venv/collections'
    assert worker.environment()['ANSIBLE_COLLECTIONS_PATH'] == str(releases[0] / '.venv/collections')


def test_lock_closes_all_collection_dependencies_and_matches_runtime():
    lock = installer.load_lock(); installer.check_runtime(lock)
    assert len(lock['collections']) == 4
    assert 'ansible-core==' + lock['ansible_core'] in (runtime.ROOT / 'constraints.txt').read_text()
    assert 'ansible-core==' + lock['ansible_core'] in (runtime.ROOT / 'requirements.txt').read_text()


def test_bad_transitive_pin_rejected_before_network(tmp_path):
    (tmp_path / 'Playbooks').mkdir()
    lock = installer.load_lock()
    lock['collections'][0]['dependencies']['missing.collection'] = '>=1.0.0'
    (tmp_path / 'Playbooks/collections.lock.json').write_text(json.dumps(lock))
    (tmp_path / 'Playbooks/requirements.yml').write_bytes((runtime.ROOT / 'Playbooks/requirements.yml').read_bytes())
    with pytest.raises(ValueError, match='missing or incompatible'):
        installer.load_lock(tmp_path)


@pytest.mark.parametrize('error,expected', [
    (TimeoutError(), True), (ConnectionResetError(), True), (socket.gaierror(-3, 'dns'), True),
    (ssl.SSLCertVerificationError('certificate'), False), (ValueError('checksum'), False),
    (urllib.error.HTTPError('https://example', 429, 'rate limit', {}, None), True),
    (urllib.error.HTTPError('https://example', 503, 'unavailable', {}, None), True),
    (urllib.error.HTTPError('https://example', 403, 'forbidden', {}, None), False),
    (urllib.error.HTTPError('https://example', 404, 'absent', {}, None), False),
])
def test_only_transient_download_failures_retry(error, expected):
    assert installer.transient(error) is expected


def test_transient_download_recovers_without_switching_version_or_source(tmp_path, monkeypatch):
    data = b'locked bytes'; item = dict(installer.load_lock()['collections'][0], sha256=hashlib.sha256(data).hexdigest())
    calls = []
    def download(url, **kwargs):
        calls.append(url)
        if len(calls) == 1: raise ConnectionResetError('fixture reset')
        return io.BytesIO(data)
    monkeypatch.setattr(installer.urllib.request, 'urlopen', download)
    monkeypatch.setattr(installer.time, 'sleep', lambda delay: None)
    log = io.StringIO()
    path = installer.obtain(item, tmp_path, log)
    assert path.read_bytes() == data and calls == [item['url']] * 2
    assert 'ConnectionResetError' in log.getvalue()


def test_retry_is_bounded_and_checksum_failure_is_not_retried(tmp_path, monkeypatch):
    item = installer.load_lock()['collections'][0]; calls = []
    def download(*a, **k):
        calls.append(1); raise TimeoutError('fixture timeout')
    monkeypatch.setattr(installer.urllib.request, 'urlopen', download)
    monkeypatch.setattr(installer.time, 'sleep', lambda delay: None)
    with pytest.raises(TimeoutError): installer.obtain(item, tmp_path, io.StringIO())
    assert len(calls) == 3
    calls.clear()
    def intercepted(*a, **k):
        calls.append(1); return io.BytesIO(b'<html>unexpected proxy response</html>')
    monkeypatch.setattr(installer.urllib.request, 'urlopen', intercepted)
    with pytest.raises(ValueError, match='checksum'): installer.obtain(item, tmp_path, io.StringIO())
    assert len(calls) == 1


def test_failed_staging_preserves_active_release_and_private_traceback(tmp_path, monkeypatch):
    active = tmp_path / 'releases/old/collections'; active.mkdir(parents=True)
    sentinel = active / 'sentinel'; sentinel.write_bytes(b'previous-release')
    current = tmp_path / 'current'; current.symlink_to(active.parent)
    target = tmp_path / 'releases/new/.venv/collections'
    offline = tmp_path / 'offline'; offline.mkdir()
    item = installer.load_lock()['collections'][0]
    (offline / (item['name'].replace('.', '-') + '-' + item['version'] + '.tar.gz')).write_bytes(b'corrupt artifact')
    def network_forbidden(*a, **k): raise AssertionError('Offline install attempted network access')
    monkeypatch.setattr(installer.urllib.request, 'urlopen', network_forbidden)
    with pytest.raises(installer.CollectionInstallError, match='checksum') as exc:
        installer.install(target, offline)
    assert 'Ansible Core: 2.20.9' in str(exc.value) and 'ansible.netcommon' in str(exc.value)
    assert current.resolve() == active.parent and sentinel.read_bytes() == b'previous-release'
    assert not target.exists()
    log = target.parent / 'ansible-install.log'
    assert log.stat().st_mode & 0o077 == 0 and 'Traceback' in log.read_text()


def test_install_refuses_to_overwrite_existing_release(tmp_path):
    target = tmp_path / 'collections'; target.mkdir(); (target / 'data').write_bytes(b'keep')
    with pytest.raises(installer.CollectionInstallError, match='already exists'): installer.install(target)
    assert (target / 'data').read_bytes() == b'keep'


def test_real_ansible_config_uses_only_explicit_root(tmp_path):
    env = runtime.environment(collections=tmp_path / 'collections', state=tmp_path / 'state')
    result = subprocess.run([runtime.executable('ansible-config'), 'dump', '--format', 'json'],
                            capture_output=True, text=True, check=True, env=env)
    config = {item['name']: item['value'] for item in json.loads(result.stdout) if 'name' in item}
    assert config['COLLECTIONS_PATHS'] == [str(tmp_path / 'collections')]
    assert config['COLLECTIONS_SCAN_SYS_PATH'] is False


def test_worker_uses_same_isolated_path_and_keeps_private_ssh_trust(app, tmp_path, monkeypatch):
    from app.services.automation import safe_environment
    selected = tmp_path / 'selected'; monkeypatch.setenv('ANSIBLE_COLLECTIONS_PATH', str(selected))
    monkeypatch.setenv('ANSIBLE_COLLECTIONS_PATHS', '/unrelated')
    with app.app_context():
        env = safe_environment(tmp_path, tmp_path / 'ansible.cfg', tmp_path / 'control')
    assert env['ANSIBLE_COLLECTIONS_PATH'] == str(selected)
    assert env['ANSIBLE_COLLECTIONS_SCAN_SYS_PATH'] == 'False'
    assert env['ANSIBLE_HOST_KEY_CHECKING'] == 'True'
    assert 'NARSIKA_ANSIBLE_KNOWN_HOSTS' in env
    assert 'ANSIBLE_COLLECTIONS_PATHS' not in env
