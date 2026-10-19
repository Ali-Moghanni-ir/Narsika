#!/usr/bin/env python3
"""Check a release tag against app/version.py and CHANGELOG.md, and write its release notes."""
import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_PATTERN = re.compile(r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(-[0-9A-Za-z.-]+)?')


def current_version(root: Path = ROOT) -> str:
    """Read __version__ without importing the application."""
    source = (root / 'app/version.py').read_text()
    match = re.search(r"^__version__ = '([^']+)'$", source, re.M)
    if not match or not VERSION_PATTERN.fullmatch(match.group(1)):
        raise ValueError('app/version.py must define __version__ as a semantic version.')
    return match.group(1)


def changelog_section(version: str, changelog: str) -> str:
    """Return the body of the "## <version>" section of the changelog."""
    heading = re.search(rf'^## {re.escape(version)}(?= |$).*$', changelog, re.M)
    if not heading:
        raise ValueError(f'CHANGELOG.md has no "## {version}" section.')
    rest = changelog[heading.end():]
    following = re.search(r'^## ', rest, re.M)
    body = (rest[:following.start()] if following else rest).strip()
    if not body:
        raise ValueError(f'The CHANGELOG.md section for {version} is empty.')
    return body


def check(tag: str, root: Path = ROOT) -> str:
    """Validate a tag such as v0.9.0 and return the release notes for it."""
    version = current_version(root)
    if tag != 'v' + version:
        raise ValueError(f'Tag {tag} does not match app/version.py ({version}); expected v{version}.')
    return changelog_section(version, (root / 'CHANGELOG.md').read_text())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('tag', help='release tag, for example v0.9.0')
    parser.add_argument('--notes', type=Path, help='write the release notes to this file')
    args = parser.parse_args()
    try:
        notes = check(args.tag)
    except ValueError as error:
        sys.exit(f'FAIL {error}')
    if args.notes:
        args.notes.write_text(notes + '\n')
    print(f'PASS {args.tag} matches app/version.py and CHANGELOG.md')


if __name__ == '__main__':
    main()
