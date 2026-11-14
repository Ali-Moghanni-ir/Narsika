"""Build network preflight and release manifest checks. No real network access."""
import hashlib
import socket
import ssl
import urllib.error
import zipfile

import pytest

from tools import network_check, package_release


def test_network_check_covers_every_build_download():
    hosts = [url.split('/')[2] for _, url in network_check.targets({})]
    s3 = 'ansible-galaxy-ng.s3.dualstack.us-east-1.amazonaws.com'
    assert hosts == ['pypi.org', 'files.pythonhosted.org', 'galaxy.ansible.com', s3, 'deb.debian.org']
    # A mirror serves its own files, but Galaxy and Debian are still needed.
    mirrored = [url.split('/')[2] for _, url in network_check.targets({'NARSIKA_PIP_INDEX_URL': 'https://mirror.example.org/simple'})]
    assert mirrored == ['mirror.example.org', 'galaxy.ansible.com', s3, 'deb.debian.org']


@pytest.mark.parametrize('error,expected', [
    (urllib.error.URLError(socket.gaierror(-3, 'Temporary failure in name resolution')), 'DNS name resolution failed'),
    (urllib.error.URLError(OSError('Tunnel connection failed: 403 Forbidden')), 'the proxy refused'),
    (urllib.error.URLError(ssl.SSLCertVerificationError(1, '[SSL: CERTIFICATE_VERIFY_FAILED]')), 'TLS certificate verification failed'),
    (urllib.error.URLError(TimeoutError('timed out')), 'timed out'),
    (urllib.error.URLError(ConnectionRefusedError(111, 'Connection refused')), 'refused'),
])
def test_network_failures_are_named(error, expected):
    assert expected in network_check.reason(error)


def test_any_http_answer_counts_as_reachable(monkeypatch):
    def answer(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 404, 'Not Found', None, None)
    monkeypatch.setattr(network_check.urllib.request, 'urlopen', answer)
    assert network_check.probe('https://files.pythonhosted.org/') is None
    # Over plain HTTP the error may come from a proxy that cannot reach the host.
    assert 'HTTP 404' in network_check.probe('http://deb.debian.org/debian/dists/bookworm/InRelease')


def test_failed_check_prints_one_cause_naming_the_host(monkeypatch, capsys):
    def probe(url):
        return 'DNS name resolution failed' if 'pythonhosted' in url else None
    monkeypatch.setattr(network_check, 'probe', probe)
    monkeypatch.delenv('NARSIKA_PIP_INDEX_URL', raising=False)
    assert network_check.main() == 3
    output = capsys.readouterr().out
    causes = [line for line in output.splitlines() if line.startswith(network_check.CAUSE)]
    assert causes == [network_check.CAUSE + 'Cannot reach files.pythonhosted.org (Python package files): DNS name resolution failed.']
    assert 'NARSIKA_PIP_INDEX_URL' in output and 'Ansible collections' in output


def test_passing_check_prints_no_cause(monkeypatch, capsys):
    monkeypatch.setattr(network_check, 'probe', lambda url: None)
    assert network_check.main() == 0
    assert network_check.CAUSE not in capsys.readouterr().out


def archive(path, files, manifest):
    with zipfile.ZipFile(path, 'w') as target:
        for name, content in files.items():
            target.writestr('narsika/' + name, content)
        target.writestr('narsika/MANIFEST.sha256', manifest)


def line(name, content):
    return hashlib.sha256(content).hexdigest() + '  ' + name + '\n'


def test_release_archive_must_list_every_shipped_file(tmp_path):
    good = tmp_path / 'good.zip'
    archive(good, {'app.py': b'code'}, line('app.py', b'code'))
    package_release.check_archive(good)
    stale = tmp_path / 'stale.zip'
    # A file such as an old launcher wrapper that ships without a manifest entry.
    archive(stale, {'app.py': b'code', 'run_win.bat': b'@echo off'}, line('app.py', b'code'))
    with pytest.raises(ValueError, match='run_win.bat'):
        package_release.check_archive(stale)
    missing = tmp_path / 'missing.zip'
    archive(missing, {}, line('app.py', b'code'))
    with pytest.raises(ValueError, match='does not contain: app.py'):
        package_release.check_archive(missing)
    changed = tmp_path / 'changed.zip'
    archive(changed, {'app.py': b'other'}, line('app.py', b'code'))
    with pytest.raises(ValueError, match='Manifest mismatch'):
        package_release.check_archive(changed)


def test_real_release_archive_passes_the_manifest_check(tmp_path, monkeypatch):
    destination = tmp_path / 'narsika.zip'
    monkeypatch.setattr('sys.argv', ['package_release.py', str(destination)])
    package_release.main()
    package_release.check_archive(destination)
    with zipfile.ZipFile(destination) as release:
        names = release.namelist()
    assert 'narsika/tools/network_check.py' in names and 'narsika/run_windows.bat' in names
    assert 'narsika/run_win.bat' not in names
