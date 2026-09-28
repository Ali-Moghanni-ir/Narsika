# Firewall Control validation — 2026-09-12

> Historical initial-preview checks below. For the subsequent installer/backend/frontend rebuild and current test totals, use [REBUILD-REVIEW-FA.md](REBUILD-REVIEW-FA.md).

Source: separate local copy of the existing Narsika working project. No GitHub commit, push, release or deployment was performed. The original local working tree was not edited for this feature.

## Evidence

| Check | Result | Boundary |
|---|---|---|
| Firewall unit/API/job tests | PASS | 52 isolated tests; real Flask/SQLite/crypto, device I/O replaced only inside tests |
| Existing application regression suite | PASS | Existing 123 passing tests retained; one network_cli transport test remains skipped by its environment prerequisite |
| Full combined suite | PASS | 175 passed, 1 skipped in 61.90 seconds; includes authorization, compiler, receipts, race handling, recovery status and retention-reference tests |
| Ansible playbook syntax | PASS | 18 plays: ten current, six preserved, two new internal vendor plays; no target connection |
| Package/static validation | PASS | Python AST, all JS module parsing, shell syntax, Compose persistence mapping, real configuration generation and Gunicorn factory loading |
| Live local Gunicorn HTTP | PASS on two successive final runs | Random bootstrap, password change, persisted changed hash, eleven pages including Firewall, new API/assets, restart and inventory persistence; password-free logs |
| Initial HTTP attempt | One unexplained failure | First attempt returned HTTP 401 during login after restart. Subsequent strengthened checks passed twice. No product fix or root-cause claim is made for that single observation; retain restart/login in lab acceptance |
| Browser interaction / visual QA | BLOCKED | Managed browser rejected loopback test URL with ERR_BLOCKED_BY_CLIENT. No screenshot, pixel-perfect or end-to-end browser claim |
| Cisco / RouterOS real firmware | LAB_REQUIRED | No device credentials or routable lab targets supplied; rule effect, exact firmware command handling and lockout recovery not tested on hardware |
| Automatic timed rollback | NOT IMPLEMENTED | Explicit in UI, API and documentation. Mandatory encrypted backup and downloadable manual recovery are implemented |
| Ubuntu fresh install / Windows / Docker build | NOT RUN for this preview | Existing launchers retained; no privileged host changes or production deployment performed |

No production seeded devices, firewall states or success results were used. `tests/ui_preview.py` is an explicitly launched disposable fixture utility, not the deployed application. It disables all target connections and starts no worker.

## Commands to reproduce with development dependencies installed

```bash
python -m pytest -q
python tools/check_playbooks.py
python tools/validate_package.py
python tools/http_smoke.py
python tools/package_release.py /absolute/output/path/narsika-firewall-review.zip
```

`ansible-playbook` must be on PATH and pinned collections installed as described by the existing project. The local authoring environment needed its already-installed dependencies/CLI paths supplied explicitly; missing Ansible on the initial PATH was an environment failure, not a product behavior fix.

## Final execution record

```text
Full suite: 175 passed, 1 skipped in 61.90 seconds.
Firewall-specific subset: 52 passed in 14.06 seconds.
JavaScript: 12 runtime modules parsed successfully.
Playbooks: 18 syntax checks passed (including two internal drivers).
Live Gunicorn HTTP: two successive strengthened smoke runs passed.
Browser visual acceptance: BLOCKED (loopback URL rejected).
Firmware execution: LAB_REQUIRED.
```

The two internal drivers use per-entry included task files so a failed entry stops the host before later entries, instead of relying on a module loop that may continue after an item failure. Hardware behavior is still a lab gate. The ZIP builder verifies its own per-file SHA-256 manifest and archive integrity. Review the lab matrix in [FIREWALL-REVIEW.md](FIREWALL-REVIEW.md) before treating software checks as operational acceptance.
