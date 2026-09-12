"""Final release contracts. All fixtures are local; no host installation is performed."""
from pathlib import Path
import re
import shlex
import struct
import subprocess
import threading
from types import SimpleNamespace

import pytest

from app.models import db, Device
from app.security import APIError
from app.services import telemetry
from app.services.automation import configuration_play, validate_operation
from tools import install_native as installer
from tools.package_release import release_files
from conftest import credential, device

ROOT = Path(__file__).resolve().parents[1]


def test_bootstrap_environment_can_execute_runuser(tmp_path, monkeypatch):
    monkeypatch.setenv('PATH', '/usr/bin:/bin')
    environment = installer.service_environment(tmp_path / 'release')
    result = installer.run('runuser', '--version', env=environment,
                           capture_output=True, text=True)
    assert result.returncode == 0
    assert 'runuser' in result.stdout.lower()


@pytest.mark.parametrize('distribution,version,architecture,supported', [
    ('ubuntu', '24.04', 'x86_64', True),
    ('ubuntu', '24.10', 'x86_64', True),
    ('ubuntu', '26.04', 'x86_64', True),
    ('ubuntu', '30.04', 'x86_64', True),
    ('ubuntu', '23.10', 'x86_64', False),
    ('ubuntu', 'development', 'x86_64', False),
    ('debian', '24.04', 'x86_64', False),
    ('ubuntu', '26.04', 'aarch64', False),
])
def test_native_platform_version_gate(distribution, version, architecture, supported):
    result = subprocess.run([
        'bash', '-c', '. "$1"; narsika_platform_supported "$2" "$3" "$4"',
        'bash', str(ROOT / 'tools/native_platform.sh'), distribution, version, architecture,
    ], capture_output=True, text=True)
    assert (result.returncode == 0) is supported


def test_native_installer_uses_distribution_default_supported_python():
    launcher = (ROOT / 'tools/run_native_linux.sh').read_text()
    installer = (ROOT / 'tools/install_native.py').read_text()
    assert 'python3.12' not in launcher
    assert 'python3.12' not in installer
    assert 'python3-venv' in launcher
    assert 'sys.version_info >= (3, 12)' in launcher
    assert 'export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin' in launcher
    assert launcher.count('command -v "$program"') == 2
    assert "sys.version_info < (3, 12)" in installer
    assert "run(sys.executable, '-m', 'venv'" in installer
    assert "':/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'" in installer
    assert "'--timeout', '120', '--retries', '10'" in installer


def test_failed_concurrent_refresh_never_returns_expired_data(admin, app, monkeypatch):
    target = device(admin, credential(admin))
    started = threading.Event()
    release = threading.Event()
    waiting = threading.Event()
    clock = [100.0]
    attempts = []
    results = []
    failures = []
    monkeypatch.setattr(telemetry, 'time', SimpleNamespace(monotonic=lambda: clock[0]))

    @telemetry.coalesced_sample
    def sample(row):
        attempts.append(row.id)
        if len(attempts) == 2:
            started.set()
            if not release.wait(5):
                raise AssertionError('The refresh test was not released.')
            raise APIError('Controlled device timeout.', 'TIMEOUT', 502)
        return {'sampled_at': 'local-test-sample', 'value': len(attempts)}

    with app.app_context():
        assert sample(db.session.get(Device, target['id']))['value'] == 1
    clock[0] += 5

    def read():
        with app.app_context():
            try:
                results.append(sample(db.session.get(Device, target['id'])))
            except APIError as error:
                failures.append(error.code)
            finally:
                db.session.remove()

    owner = threading.Thread(target=read)
    follower = threading.Thread(target=read)
    owner.start()
    try:
        assert started.wait(5)
        with telemetry.SAMPLE_LOCK:
            signal = next(iter(app.extensions['telemetry_pending'].values()))
            real_wait = signal.wait

            def observed_wait(timeout=None):
                waiting.set()
                return real_wait(timeout)

            monkeypatch.setattr(signal, 'wait', observed_wait)
        follower.start()
        assert waiting.wait(5)
    finally:
        release.set()
        owner.join(5)
        if follower.ident is not None:
            follower.join(5)
    assert not owner.is_alive() and not follower.is_alive()
    assert results == []
    assert sorted(failures) == ['SAMPLE_FAILED', 'TIMEOUT']
    assert app.extensions['telemetry_pending'] == {}
    with app.app_context():
        assert sample(db.session.get(Device, target['id']))['value'] == 3


@pytest.mark.parametrize('protocol,port', [('ip', None), ('icmp', None), ('tcp', 443)])
def test_routeros_equivalence_checks_absent_selectors(app, protocol, port):
    target = SimpleNamespace(platform='mikrotik')
    with app.app_context():
        parameters = validate_operation('acl', {
            'name': 'Management', 'protocol': protocol, 'action': 'permit',
            'source': 'any', 'destination': 'any', 'port': port,
        }, target)
        task = configuration_play('acl', parameters, target)[0]['tasks'][0]
    command = task['community.routeros.command']['commands'][0]
    for field in ('src-address', 'dst-address'):
        assert f'get $ids {field}]] != ""' in command
    expected_protocol = '' if protocol == 'ip' else protocol
    assert f'get $ids protocol]] != "{expected_protocol}"' in command
    expected_port = '' if port is None else str(port)
    assert f'get $ids dst-port]] != "{expected_port}"' in command
    assert 'get $ids disabled]] != "false"' in command
    assert 'Existing rule conflicts; review manually' in command


@pytest.mark.parametrize('case', ['missing_group', 'wrong_group', 'shared_primary', 'shared_member', 'privileged', 'root', 'login_shell'])
def test_service_account_validation_preserves_ubuntu_users(monkeypatch, case):
    account = SimpleNamespace(pw_name='narsika', pw_uid=12000, pw_gid=12000, pw_shell='/usr/sbin/nologin')
    primary = SimpleNamespace(gr_name='narsika', gr_gid=12000, gr_mem=[])
    users = [account]
    groups = [primary]
    if case == 'wrong_group':
        primary.gr_name = 'shared-service-users'
    elif case == 'shared_primary':
        users.append(SimpleNamespace(pw_name='someone', pw_gid=12000))
    elif case == 'shared_member':
        primary.gr_mem = ['someone']
    elif case == 'privileged':
        groups.append(SimpleNamespace(gr_name='docker', gr_gid=999, gr_mem=['narsika']))
    elif case == 'root':
        account.pw_uid = 0
    elif case == 'login_shell':
        account.pw_shell = '/bin/bash'

    def get_group(gid):
        if case == 'missing_group':
            raise KeyError(gid)
        return primary

    monkeypatch.setattr(installer.grp, 'getgrgid', get_group)
    monkeypatch.setattr(installer.grp, 'getgrall', lambda: groups)
    monkeypatch.setattr(installer.pwd, 'getpwall', lambda: users)
    # Validation has no mutation path and must never modify someone else's account.
    monkeypatch.setattr(installer, 'run', lambda *args, **kwargs: pytest.fail('Unexpected host mutation'))
    with pytest.raises(SystemExit):
        installer.validate_service_account(account)


def test_dedicated_service_account_does_not_depend_on_calling_ubuntu_user(monkeypatch):
    account = SimpleNamespace(pw_name='narsika', pw_uid=12000, pw_gid=12000, pw_shell='/usr/sbin/nologin')
    group = SimpleNamespace(gr_name='narsika', gr_gid=12000, gr_mem=[])
    monkeypatch.setattr(installer.grp, 'getgrgid', lambda gid: group)
    monkeypatch.setattr(installer.grp, 'getgrall', lambda: [group])
    monkeypatch.setattr(installer.pwd, 'getpwall', lambda: [account])
    for username in ('root', 'desktop-user', 'another-user'):
        monkeypatch.setenv('USER', username)
        assert installer.validate_service_account(account) is None


def test_docker_copy_sources_are_available_in_build_context():
    ignores = (ROOT / '.dockerignore').read_text().splitlines()
    for line in (ROOT / 'Dockerfile').read_text().splitlines():
        if not line.startswith('COPY '):
            continue
        parts = [value for value in shlex.split(line)[1:] if not value.startswith('--')]
        for name in parts[:-1]:
            assert (ROOT / name).exists(), name
            if name.startswith('tools/') and 'tools/*' in ignores:
                assert '!' + name in ignores, f'Docker build excludes {name}'


def test_final_readme_and_brand_assets_are_packaged(admin, client):
    readme = (ROOT / 'README.md').read_text()
    paths = {path.relative_to(ROOT).as_posix() for path in release_files()}
    assert 'README.md' in paths and 'README-GITHUB.md' not in paths
    assert not (ROOT / 'README-GITHUB.md').exists()
    assert 'storage,codes' not in readme and '-+-' not in readme
    assert '## Documentation' not in readme
    assert '## License' not in readme
    assert 'Public Beta · Free to use, always.' in readme
    assert 'run_windows.bat -WSL' in readme and 'bash run_linux.sh' in readme
    links = re.findall(r'!\[[^\]]*\]\(([^)]+)\)|<img[^>]*src="([^"]+)"', readme)
    for markdown, html in links:
        assert (markdown or html) in paths
    for filename, expected in [('narsika-relay.png', (1024, 1024)), ('narsika-logo-180.png', (180, 180))]:
        raw = (ROOT / 'app/static/img' / filename).read_bytes()
        assert raw[:8] == b'\x89PNG\r\n\x1a\n'
        assert struct.unpack('>II', raw[16:24]) == expected
        assert raw[25] == 6  # PNG RGBA preserves a real alpha channel.
    for name in ('Cisco', 'MikroTik', 'Original'):
        assert len([p for p in paths if p.startswith('Playbooks/' + name + '/') and p.endswith('.yml')]) == (6 if name == 'Original' else 5)
    for path in paths:
        assert not any(part in ('__pycache__', '.venv', 'instance', '.git') for part in Path(path).parts)
        assert not path.endswith(('.pyc', '.db', '.db-wal', '.db-shm', '.sqlite3'))
        assert Path(path).name != '.env'
    shell = admin.get('/index.html').get_data(as_text=True)
    assert '/static/img/narsika-logo-180.png' in shell
    assert '/static/img/narsika-relay.png' not in shell
    auth = (ROOT / 'app/templates/auth_base.html').read_text()
    assert 'narsika-relay.png' in auth  # The large sign-in illustration is preserved.
    assert client.get('/static/img/narsika-logo-180.png').status_code == 200
