"""Explicit HTTPS package source and bounded downloads; never alter version pins."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlsplit


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


def install(python, requirements, options, interactive=True):
    selected = dict(options)
    for attempt in range(3):
        print('Python package source: ' + selected['index'], flush=True)
        print(f"Download timeout: {selected['timeout']}s; retries: {selected['retries']}. TLS verification remains enabled.", flush=True)
        result = subprocess.run(command(python, requirements, selected), env=environment())
        if result.returncode == 0:
            subprocess.run([str(python), '-m', 'pip', 'check'], check=True, env=environment())
            return selected
        print('Package installation failed. A resolver error can also mean a pinned version is unavailable on this index.')
        print('No version constraints were relaxed. Service and database activation have not started.')
        if not interactive or attempt == 2:
            break
        value = input('Retry this staged release with an HTTPS mirror URL, or Enter to stop: ').strip()
        if not value:
            break
        selected['index'] = index_url(value)
    raise RuntimeError('Python dependencies could not be installed. See docs/INSTALLATION.md; the staged release is retained.')


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
