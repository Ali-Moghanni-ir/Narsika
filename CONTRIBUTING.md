# Contributing to Narsika

Thanks for helping. Narsika manages real network equipment, so changes are held to a high bar for safety, honesty and testability.

## Ways to help

- **Report bugs** with the bug report template. Include your install method, OS, device platform and firmware, and redacted `sudo narsika-admin doctor` output.
- **Share lab results.** Reports of what worked or failed on specific Cisco IOS / IOS XE and RouterOS versions are especially valuable.
- **Suggest features** with the feature request template, starting from the problem you want to solve.
- **Send pull requests** for bugs, documentation and roadmap items. For larger changes, open an issue first so the approach can be agreed.

Never post passwords, keys, full device configurations or other secrets in issues or pull requests. Report security problems privately, as described in [SECURITY.md](SECURITY.md).

## Development setup

Python 3.12, 3.13 or 3.14 and Node.js (for frontend tests only):

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
python tools/install_collections.py --collections-path "$PWD/.collections"
export ANSIBLE_COLLECTIONS_PATH="$PWD/.collections"
```

Run the same checks as CI before opening a pull request:

```bash
python -m pytest -q
node --test tests/frontend_core.test.cjs
python tools/check_playbooks.py
python tools/validate_package.py
python tools/http_smoke.py
```

To look at the interface without devices, see [Previewing the interface](docs/FIREWALL.md#previewing-the-interface-without-devices).

## Rules that must not be broken

1. **No fabricated data** — no demo records, fake telemetry or simulated results. Unknown values are `null` and shown as N/A.
2. **Secrets stay secret** — encrypted at rest; never in argv, logs, audit events, exceptions or API responses.
3. **SSH trust** — never auto-accept unknown host keys; reject changed keys.
4. **Authorization on the server** — every endpoint declares its required role; every mutation requires CSRF.
5. **Device changes** go through validation, the job queue and the audit log. Nothing writes to devices outside this path.
6. **Migrations are additive** — never drop tables or columns; snapshot before changing data; keep legacy URLs working.
7. **Honest status** — distinguish written, tested, CI-passed and lab-verified. Don't claim a device operation works on hardware unless it was verified there.
8. **No new infrastructure** (Redis, Celery, Node build, SPA framework, external database) without an agreed decision.

## Code style

- New and modified Python: readable PEP 8, one statement per line, type hints on new functions, short docstrings where intent isn't obvious.
- Don't reformat code you are not otherwise changing; formatting-only changes go in their own pull request.
- Keep dependencies minimal and pinned in `requirements.txt` and `constraints.txt`.
- Every behaviour change needs tests. Tests must never contact a real device.
- Code, comments, commit messages, documentation and UI text are in English.

## Commits and pull requests

- Branch from `main`: `feat/…`, `fix/…`, `refactor/…`, `docs/…` or `chore/…`.
- Use [Conventional Commits](https://www.conventionalcommits.org/): `feat: add Linux server health checks`.
- Keep pull requests focused, fill in the template, and make sure CI is green.
- Update documentation and [CHANGELOG.md](CHANGELOG.md) when behaviour changes.

Some tests pin release content on purpose — for example `tests/test_release_integration.py` checks the README and the number of bundled Playbooks. Update them deliberately when you change those things.

## License of contributions

Narsika is licensed under [FSL-1.1-ALv2](LICENSE), and `callback_plugins/` under GPL-3.0-or-later. By submitting a contribution you agree that it is licensed under the same terms as the files it changes. A Contributor License Agreement may be introduced later; if so, it will be announced here before it applies.
