# CLAUDE.md

Guidance for Claude Code when working in the Narsika repository.

## What Narsika is

Free, source-available, self-hosted network operations workspace for Cisco IOS/IOS XE and MikroTik RouterOS (Linux servers and an AI assistant are planned). Flask + Jinja + Vanilla JS + SQLite + Ansible. Public Beta.

License: FSL-1.1-ALv2 (source-available, not OSI open source). Never describe Narsika as "open source" in docs, README or UI. `callback_plugins/` is GPL-3.0-or-later because it subclasses Ansible code.

## Working with the owner

- The owner (Ali) is an experienced network engineer and a beginner programmer. Talk to him in **Persian**. Don't explain networking basics; do explain code changes plainly: what changed, why, and how to verify it.
- Everything written into the repository is **English**: code, comments, commit messages, PR descriptions, docs, UI strings.
- Work in small, reviewable steps. Before large refactors or anything hard to undo, show the plan and wait for approval.
- If an idea is risky (security, data loss, scalability), say so directly and propose an alternative.

## Commands

```bash
python -m pip install -r requirements-dev.txt          # Python 3.12-3.14
python tools/install_collections.py --collections-path "$PWD/.collections"   # checksum-locked, applies patch_ansible
ANSIBLE_COLLECTIONS_PATH="$PWD/.collections" python -m pytest -q
python tools/check_playbooks.py      # ansible-playbook --syntax-check for all bundled playbooks
python tools/validate_package.py     # release files, syntax, config generation, gunicorn load
python tools/http_smoke.py           # real Gunicorn HTTP smoke test
node --test tests/frontend_core.test.cjs   # frontend behaviour tests (Node only for tests)
```

Run the test suite before reporting any change as done. CI (`.github/workflows/validate.yml`) must stay green.

## Architecture map

- `app/__init__.py` — factory, config validation, request guards, worker startup
- `app/api.py` — core `/api` endpoints; `app/firewall_api.py` — `/api/firewall`; `app/schedules_api.py` — `/api/schedules`; `app/routes.py` — pages + legacy URLs
- `app/models.py` — SQLAlchemy models; `app/migration.py` — additive migrations (schema v2)
- `app/security.py` — Fernet encrypt/decrypt, CSRF, `require()` role decorator, validators, audit
- `app/services/` — `network` (SSH, host keys, locks), `telemetry` (health/interfaces/SNMP), `discovery`, `automation` (operation validation + Ansible), `jobs` (SQLite queue, 2 threads), `backups`, `catalog`, `firewall` (review → backup → apply → verify; internal playbooks in `firewall_playbooks/`), `scheduling`
- `callback_plugins/narsika_safe.py` — sanitised Ansible events
- `Playbooks/` — Cisco, MikroTik, Original (legacy), `examples/*.vars.yml`
- `tools/` — installers, bootstrap, admin CLI, packaging, smoke tests, maintenance

Runtime: one Gunicorn worker (8 threads) per data directory, enforced by a file lock. Never enable multiple workers, preload or reload against the SQLite volume.

## Rules that must not be broken

1. **No fabricated data**: no demo records, fake telemetry or simulated results. Unknown values are `null` → `N/A`.
2. **Secrets**: encrypted at rest with the installation Fernet key; never in argv, logs, audit events, exceptions or API responses. Never regenerate or change the encryption key.
3. **SSH trust**: never auto-accept unknown host keys; reject changed keys. Keep host key checking enabled.
4. **Authorization on the server**: every endpoint uses `require('read'|'operate'|'admin')`; every mutation requires CSRF. UI hiding is not security.
5. **Device changes** go through validation → job queue → audit. Nothing (including future AI features) writes to devices outside this path.
6. **Migrations are additive**: never drop tables/columns; snapshot before changing data; keep legacy routes working.
7. **Honest status**: distinguish written / tested / CI-passed / lab-verified. Never claim a device operation works on real hardware unless it was verified there.
8. Don't add infrastructure (Redis, Celery, Node build, SPA framework, external DB) without an explicit, agreed decision.

## Code style

- New and modified code: readable PEP 8, one statement per line, no `;` chaining, no one-line `try:`/`if` blocks, type hints on new functions, short docstrings where intent isn't obvious.
- Don't reformat untouched code inside a feature change; formatting-only changes go in their own PR.
- Keep dependencies minimal and pinned (`requirements.txt` + `constraints.txt`).
- Every behaviour change comes with tests.

## Git workflow

- Never commit directly to `main`; never force-push or rewrite `main` history.
- One branch per change: `feat/…`, `fix/…`, `refactor/…`, `docs/…`, `chore/…`.
- Conventional Commits (`feat: add Linux server health checks`). Small, logical commits.
- Open a PR per change; wait for CI.

## Known traps

- `tests/test_release_integration.py` asserts README content (License section required, no `## Documentation`, no "open source" wording) and exact playbook counts per vendor. Update those tests deliberately when you change README or add playbooks.
- `tools/package_release.py` ships only whitelisted root files (`ROOT_FILES`) and directories (`DIRECTORIES`). New root-level files that must be shipped need to be added there.
- `tools/patch_ansible.py` checks the exact upstream source hash of `ansible.netcommon`; upgrading that collection requires updating the patch and `Playbooks/collections.lock.json`.
- `tools/install_native.py` exits on Python < 3.12, which also stops pytest collection on older interpreters.
- Vendor logic is currently hard-coded (`'cisco'`/`'mikrotik'` checks across services). Adding a vendor should start with a platform driver abstraction.
