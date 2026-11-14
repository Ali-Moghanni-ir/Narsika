"""Explicit HTTPS package source and bounded downloads; never alter version pins."""
import argparse
import collections
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlsplit

# Launchers search the build output for this prefix and repeat the cause in their final error.
CAUSE = 'NARSIKA_SETUP_CAUSE: '
NETWORK_HELP = ('Set HTTPS_PROXY/HTTP_PROXY for the launcher, use a VPN in TUN mode that also handles DNS, '
                'set the Docker Desktop proxy, or use NARSIKA_PIP_INDEX_URL with a trusted HTTPS mirror.')
# Checked in order: network markers win, because pip also reports a resolver error
# when it could not download anything at all.
FAILURES = (
    ('proxy', re.compile(r'ProxyError|Cannot connect to proxy|Tunnel connection failed|407 Proxy Authentication', re.I)),
    ('dns', re.compile(r'name resolution|Name or service not known|nodename nor servname|getaddrinfo failed'
                       r'|\[Errno -[23]\]|\[Errno 11001\]|No address associated with hostname', re.I)),
    ('tls', re.compile(r'CERTIFICATE_VERIFY_FAILED|SSLError|certificate verify failed', re.I)),
    ('connection', re.compile(r'Connection refused|timed out|ConnectTimeout|ReadTimeout|NewConnectionError'
                              r'|Network is unreachable|Connection reset|Connection aborted|RemoteDisconnected', re.I)),
    ('resolver', re.compile(r'ResolutionImpossible|No matching distribution found|Could not find a version that satisfies', re.I)),
)


def index_url(value):
    try:
        parsed = urlsplit(value)
        valid = (parsed.scheme == 'https' and parsed.hostname and parsed.port != 0
                 and not parsed.username and not parsed.password
                 and not parsed.query and not parsed.fragment
                 and not any(c.isspace() or ord(c) < 32 for c in value))
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise ValueError('Use an HTTPS package index URL without credentials, query or fragment.')
    return value.rstrip('/') + '/'


def settings(environ=None):
    env = os.environ if environ is None else environ
    url = index_url(env.get('NARSIKA_PIP_INDEX_URL') or env.get('PIP_INDEX_URL') or 'https://pypi.org/simple/')
    def number(primary, fallback, default, low, high):
        try:
            value = int(env.get(primary) or env.get(fallback) or default)
        except ValueError:
            raise ValueError(primary + ' must be an integer.') from None
        if not low <= value <= high:
            raise ValueError(f'{primary} must be between {low} and {high}.')
        return value
    return dict(index=url,
                timeout=number('NARSIKA_PIP_TIMEOUT', 'PIP_DEFAULT_TIMEOUT', 120, 1, 600),
                retries=number('NARSIKA_PIP_RETRIES', 'PIP_RETRIES', 3, 0, 20))


def command(python, requirements, options, check_only=False):
    args = [str(python), '-m', 'pip', 'install', '--disable-pip-version-check',
            '--index-url', options['index'], '--timeout', str(options['timeout']),
            '--retries', str(options['retries']), '-r', str(requirements)]
    if check_only:
        args += ['--dry-run', '--ignore-installed']
    return args


def environment():
    # A selected mirror is the only index. Do not mix global/user pip configuration
    # or inherit a trusted-host override that disables certificate verification.
    env = {k: v for k, v in os.environ.items() if not k.startswith('PIP_')}
    env['PIP_CONFIG_FILE'] = os.devnull
    return env


def failed_host(output: str, index: str) -> str:
    """The host named in pip's last connection error, or the index host."""
    hosts = re.findall(r"host='([^']+)'", output) + re.findall(r'Could not fetch URL https?://([^/:\s]+)', output)
    return hosts[-1] if hosts else (urlsplit(index).hostname or index)


def classify(output: str, index: str) -> tuple[str, str]:
    """(kind, one-line cause) for a failed pip run; kind is proxy, dns, tls, connection, resolver or unknown."""
    kind = next((name for name, pattern in FAILURES if pattern.search(output)), 'unknown')
    host = failed_host(output, index)
    messages = {
        'proxy': f'The proxy refused the connection or could not reach {host}. Check HTTPS_PROXY and that the proxy is reachable from Docker containers.',
        'dns': f'Cannot resolve {host}: DNS name resolution failed in the build network. ' + NETWORK_HELP,
        'tls': f'TLS certificate verification failed for {host}; something may be intercepting HTTPS. Verification stays enabled. '
               'Use a trusted mirror with NARSIKA_PIP_INDEX_URL or fix the interception.',
        'connection': f'Cannot connect to {host} (refused, reset or timed out). ' + NETWORK_HELP,
        'resolver': f'The pinned package versions are not all available on {index}. The index may be an incomplete '
                    'mirror; no version constraints were relaxed.',
        'unknown': 'pip failed; see its output above.',
    }
    return kind, messages[kind]


def run_pip(args: list, env: dict) -> tuple[int, str]:
    """Run pip, showing its output live, and return its exit code and the end of that output."""
    process = subprocess.Popen(args, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, errors='replace')
    tail = collections.deque(maxlen=500)
    for line in process.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()
        tail.append(line)
    return process.wait(), ''.join(tail)


def install(python, requirements, options, interactive=True):
    selected = dict(options)
    cause = 'pip failed; see its output above.'
    for attempt in range(3):
        print('Python package source: ' + selected['index'], flush=True)
        print(f"Download timeout: {selected['timeout']}s; retries: {selected['retries']}. TLS verification remains enabled.", flush=True)
        code, output = run_pip(command(python, requirements, selected), environment())
        if code == 0:
            subprocess.run([str(python), '-m', 'pip', 'check'], check=True, env=environment())
            return selected
        kind, cause = classify(output, selected['index'])
        print('Package installation failed: ' + cause, flush=True)
        print(CAUSE + 'Python packages: ' + cause, flush=True)
        print('No version constraints were relaxed. Service and database activation have not started.')
        if not interactive or attempt == 2:
            break
        value = input('Retry this staged release with an HTTPS mirror URL, or Enter to stop: ').strip()
        if not value:
            break
        selected['index'] = index_url(value)
    raise RuntimeError('Python dependencies could not be installed: ' + cause + ' See docs/INSTALLATION.md; the staged release is retained.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python', default=sys.executable, help='Existing Narsika venv Python path')
    parser.add_argument('--index-url')
    parser.add_argument('--requirements', type=Path, default=Path(__file__).resolve().parents[1] / 'requirements.txt')
    args = parser.parse_args()
    options = settings()
    if args.index_url:
        options['index'] = index_url(args.index_url)
    print('Checking dependency resolution only; nothing will be installed.')
    raise SystemExit(subprocess.run(command(args.python, args.requirements, options, True), env=environment()).returncode)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError) as error:
        raise SystemExit('Dependency check stopped: ' + str(error)) from None
