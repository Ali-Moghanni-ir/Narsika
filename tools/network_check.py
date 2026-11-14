#!/usr/bin/env python3
"""Network preflight for the Docker build, run in a throwaway container of the pinned base image.

It uses only the standard library and the same proxy environment the build gets,
so a failure here predicts a failure of the long build. It reads nothing private
and changes nothing.
"""
import concurrent.futures
import os
import socket
import ssl
import sys
import urllib.error
import urllib.request
from urllib.parse import urlsplit

CAUSE = 'NARSIKA_SETUP_CAUSE: '
TIMEOUT = 8
HINTS = ('Make the host reachable from Docker containers, then rerun the launcher. Options:',
         '  - Set HTTPS_PROXY/HTTP_PROXY before running the launcher; it passes them to the build only.',
         '  - Use a VPN in TUN mode that also handles DNS for Docker Desktop (WSL2).',
         '  - Set the proxy in Docker Desktop: Settings > Resources > Proxies.',
         '  - For Python packages only: NARSIKA_PIP_INDEX_URL=<trusted HTTPS mirror>. It does not cover the Ansible collections.')


def targets(environ: dict) -> list[tuple[str, str]]:
    """(purpose, URL) pairs the image build downloads from."""
    index = (environ.get('NARSIKA_PIP_INDEX_URL') or 'https://pypi.org/simple/').rstrip('/') + '/'
    result = [('Python package index', index + 'pip/')]
    if urlsplit(index).hostname == 'pypi.org':
        # pypi.org only lists packages; the wheels come from this host.
        result.append(('Python package files', 'https://files.pythonhosted.org/'))
    result.append(('Ansible collections', 'https://galaxy.ansible.com/api/'))
    # shortcut: Galaxy currently redirects collection downloads to this S3 host; follow a lock-file URL's redirects if that changes.
    result.append(('Ansible collection files', 'https://ansible-galaxy-ng.s3.dualstack.us-east-1.amazonaws.com/'))
    result.append(('Debian packages', 'http://deb.debian.org/debian/dists/bookworm/InRelease'))
    return result


def reason(error: BaseException) -> str:
    """A short, user-facing description of a network failure."""
    seen = error
    while isinstance(seen, urllib.error.URLError) and not isinstance(seen, urllib.error.HTTPError):
        seen = seen.reason
    text = str(seen)
    if isinstance(seen, socket.gaierror) or 'name resolution' in text.lower() or 'getaddrinfo' in text.lower():
        return 'DNS name resolution failed'
    if 'tunnel connection failed' in text.lower() or 'proxy' in text.lower():
        return 'the proxy refused the connection or could not reach the host (' + text + ')'
    if isinstance(seen, ssl.SSLCertVerificationError) or 'CERTIFICATE_VERIFY_FAILED' in text:
        return 'TLS certificate verification failed; HTTPS may be intercepted (verification stays enabled)'
    if isinstance(seen, (TimeoutError, socket.timeout)) or 'timed out' in text.lower():
        return 'the connection timed out'
    if isinstance(seen, ConnectionRefusedError) or 'refused' in text.lower():
        return 'the connection was refused'
    if isinstance(seen, OSError) and 'unreachable' in text.lower():
        return 'the network is unreachable'
    return text or type(seen).__name__


def probe(url: str) -> str | None:
    """None when the host answers with any HTTP status, else the failure reason."""
    request = urllib.request.Request(url, headers={'User-Agent': 'narsika-network-check'})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            response.read(1)
    except urllib.error.HTTPError as error:
        # Over HTTPS an error status comes from the host itself, so DNS, routing, proxy and TLS work.
        # Over plain HTTP a proxy answers on the host's behalf, so its error status proves nothing.
        return None if url.startswith('https:') else f'HTTP {error.code} from the proxy or server'
    except (urllib.error.URLError, OSError, ValueError) as error:
        return reason(error)
    return None


def main() -> int:
    checks = targets(os.environ)
    proxy = os.environ.get('HTTPS_PROXY') or os.environ.get('https_proxy')
    print('Network check' + (' through the build proxy' if proxy else ' without a proxy') + ':', flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(checks)) as pool:
        results = list(pool.map(lambda item: probe(item[1]), checks))
    failed = []
    for (purpose, url), failure in zip(checks, results):
        host = urlsplit(url).hostname
        print(f'  {"OK  " if failure is None else "FAIL"} {host} ({purpose})' + (f': {failure}' if failure else ''), flush=True)
        if failure:
            failed.append((host, purpose, failure))
    if not failed:
        return 0
    host, purpose, failure = failed[0]
    others = ''.join(f'; also {item[0]}' for item in failed[1:])
    print(CAUSE + f'Cannot reach {host} ({purpose}): {failure}{others}.', flush=True)
    for line in HINTS:
        print(line, flush=True)
    return 3


if __name__ == '__main__':
    sys.exit(main())
