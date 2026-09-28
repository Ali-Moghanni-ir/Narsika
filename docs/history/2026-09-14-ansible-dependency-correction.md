# Ansible dependency correction — 2026-09-14

This report describes the current local review package. It supersedes earlier Ansible/Python installation guidance and historical dependency test counts. No source was published to GitHub by this correction. Application routes, models, playbooks, scheduler behavior and persistent data were preserved.

## Root Cause

### Confirmed in source and reproduction

1. **Galaxy response-cache bug.** In the pinned `ansible-core 2.19.12`, `_call_galaxy` creates a cache entry containing `expires` and `paginated` before completing the HTTP request. A subsequent lookup assumes that entry also contains `results`. An incomplete entry can therefore raise the reported `KeyError` at `res = path_cache['results']`. This is an internal cache lookup, not proof that the server returned JSON without a `results` property. The same unchecked read exists in 2.20.9; upgrading core alone is insufficient. An automated test reproduces the exception with an incomplete cache on Python 3.12 and 3.14 and verifies that `no_cache=True` bypasses it without modifying the cache file. The production installation now avoids API resolution entirely.
2. **Missing installation environment.** Native setup passed `-p <release>/.venv/collections` to Galaxy without setting its configured collection root. This explains the warning about `/root/.ansible/collections:/usr/share/ansible/collections`. The former systemd service already set a runtime path; it would be inaccurate to say every previous production job necessarily used the wrong root. CI and manual/development invocations also lacked consistent setup, while workers inherited configuration from their launching environment.
3. **Unsupported controller combination.** The source pinned core 2.19.12 but accepted every Python >=3.12, including the reported 3.14 controller. Official core 2.19 controller support is 3.11–3.13; core 2.20 supports 3.12–3.14. This is a compatibility defect independent of the cache crash, which is reproducible on 3.12 too.
4. **Divergent secondary requirements.** `Playbooks/requirements.txt` allowed core >=2.18,<2.20 and Paramiko >=3.5,<4.0, conflicting with the application runtime. It now delegates to the application's pinned requirements. The four collection versions were already pinned; they were verified and retained, not upgraded arbitrarily.

### Not established on the user's server

The initial trigger for an incomplete cache remains unknown without the first underlying exception and the original server environment. Network reset, timeout, interrupted request, a previous cache entry or an intercepted response are possible. No evidence establishes a Galaxy API v2/v3 change, rate limit, HTML response, resolvelib conflict or Python 3.14 as the direct cause of this traceback. Current official v3 metadata and all four artifact downloads succeeded in this workspace. The user's `/root/.ansible` and server caches were neither accessed nor deleted.

Primary sources: [official support matrix](https://docs.ansible.com/projects/ansible/latest/reference_appendices/release_and_maintenance.html), [core 2.20.9 release metadata](https://pypi.org/project/ansible-core/2.20.9/), [upstream report of the same traceback on Python 3.13](https://github.com/ansible/ansible/issues/85918). Artifact URLs and official SHA-256 values are recorded in `Playbooks/collections.lock.json`.

## Dependency Versions

| Component | Selected version / policy |
|---|---|
| Native controller Python | Supported minor versions 3.12, 3.13, 3.14; reject <3.12 and >=3.15 |
| Python tested here | 3.12.14 and 3.14.7 |
| Docker Python | 3.12.14-slim-bookworm, pinned manifest digest in Dockerfile |
| ansible-core | 2.20.9, released 2026-09-08 |
| ansible community meta-package | Not installed; core supplies the CLI tools |
| resolvelib | 1.2.1; satisfies core 2.20.9's >=0.8.0,<2.0.0 requirement |
| packaging | 26.3 |
| ansible-pylibssh | 1.4.0; existing known-hosts adaptation retained |
| ansible.netcommon | 8.6.2; core >=2.16.0, requires ansible.utils >=3.0.0 |
| ansible.utils | 6.1.0; core >=2.16.0, no collection dependencies |
| cisco.ios | 11.5.0; core >=2.16.0, requires ansible.netcommon >=8.5.2 |
| community.routeros | 3.21.0; core >=2.15.0, requires ansible.netcommon >=1.0.0 |
| community.general | Not required or installed |

The collection dependency closure is complete. Both its ranges and each collection's `meta/runtime.yml` are checked against the lock. Native Python patch versions follow Ubuntu security updates; they are recorded in the installation receipt rather than forcing an unrelated interpreter build. OS package repositories and existing Python wheel constraints are not content-addressed snapshots. This correction makes collection contents deterministic; it does not claim the entire operating-system image is bit-for-bit reproducible.

## Collection Architecture

Native layout:

```text
<release>/.venv/collections/ansible_collections/<namespace>/<collection>
<release>/.venv/collections/narsika-install.json
<release>/.venv/ansible-install.log
```

Docker collections are image-owned under `/opt/ansible/collections`, not a shared host volume. Development defaults to the active virtual environment's `collections` directory. A development/CI override accepts one absolute `ANSIBLE_COLLECTIONS_PATH`; native releases always select their own root. The configured path is the parent of `ansible_collections`, not its child.

Installation sequence:

1. Validate the pinned core, Python minor, requirements/lock agreement and closed dependency graph.
2. Fetch exact official artifact URLs, with at most three attempts and 45-second socket timeout for transient errors. Downloads are bounded to 64 MiB and checked against a 180-second elapsed budget between reads. A blocked read is additionally bounded by the socket timeout. HTTP 408/429/500/502/503/504, timeouts, DNS and connection errors can retry; certificate/checksum failures and other HTTP errors do not. Sources and versions never switch automatically.
3. Verify each artifact's SHA-256 against the committed lock. Alternatively read the same archives from an explicitly selected read-only directory, with identical hash checks and no downloads.
4. Generate a temporary requirements file pointing to these local archives. Run the release's own `ansible-galaxy collection install --offline --no-cache --no-deps -r <temporary-requirements> -p <staging-root>`. `--no-deps` is safe here because the complete pinned closure was validated; no unpinned dependency can be downloaded. Local installation has a 180-second process timeout and is not retried.
5. Validate installed versions/dependencies, apply the existing guarded SSH known-hosts adaptation, check the actual Ansible config and load the Cisco/RouterOS modules plus netcommon network_cli plugin.
6. Write a receipt recording Python, core, lock digest and artifact digests. Publish the collection directory atomically on the same filesystem. An existing destination is never overwritten. Temporary archives and only this attempt's state are cleaned up.
7. Native deployment syntax-checks all 18 bundled/internal playbooks before stopping the prior service. Existing activation, health and rollback logic remains in place.

Every job receives `ANSIBLE_COLLECTIONS_PATH` (singular), `ANSIBLE_COLLECTIONS_SCAN_SYS_PATH=False`, its own private config/temp/home and the existing sanitized callback/SSH trust settings. Inherited global Ansible overrides are discarded. The real release root and Python prefix are captured at import, so a later `current` symlink change cannot redirect an already-running worker. Scheduled jobs use the same runner. The internal `ansible-config dump` setting is still named `COLLECTIONS_PATHS`; that is not use of the deprecated plural environment variable.

Full Galaxy diagnostic output and original tracebacks are retained in a mode-0600 log. The terminal reports the collection/stage, official source, core/Python versions, failure category and log location. A download or validation failure leaves the active release pointer unchanged. Previous releases and all unrelated caches remain untouched. Docker caches the dependency layer across application-only changes; a shared mutable collection installation is not introduced.

## Installation and diagnostics

For native upgrades, extract this package separately and run `bash run_linux.sh`. It stages a new release; do not run pip upgrades or collection installers inside an active release. Existing accounts, encryption keys and data are preserved by the existing installer. This change does not require deleting a failed release or any user home/cache.

For a fresh development environment, from the project root:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
python tools/install_collections.py
export ANSIBLE_COLLECTIONS_PATH="$PWD/.venv/collections"
export ANSIBLE_COLLECTIONS_SCAN_SYS_PATH=False
export ANSIBLE_CONFIG="$PWD/ansible.cfg"
python tools/install_collections.py --verify-only
ansible --version
ansible-config dump --only-changed
ansible-galaxy collection list
python tools/check_playbooks.py
python -m pytest -q -rs
```

`--verify-only` checks versions, configuration and plugin loading without installing or replacing dependencies. If the destination already exists, verify it or use a new virtual environment/release; the installer intentionally does not overwrite it.

For collection-only offline installation, place the four files named by the lock in an existing directory:

```bash
python tools/install_collections.py --artifact-dir /absolute/path/to/artifacts
# Native installer also preserves this option across sudo:
NARSIKA_COLLECTION_ARTIFACT_DIR=/absolute/path/to/artifacts bash run_linux.sh
```

The source archive directory is read-only to the installer; files are copied and verified in private staging. This is not a completely offline apt/pip/base-image installation. Missing/corrupt artifacts fail explicitly and never trigger a network fallback. Do not copy mutable installed collection trees from another release.

After native deployment, `sudo narsika-admin doctor` checks the active release's pinned runtime, configured collection path and vendor/plugin loading. For manual CLI diagnostics, use that release's `.venv/bin` executables and explicitly export its absolute collection/config paths as above. A pip mirror controls Python downloads only; it does not mirror Galaxy artifacts.

## Verification

Current command results and test boundaries are recorded in `ANSIBLE-TEST-RESULTS.txt` alongside this report. Verified with real core 2.20.9 and real collection archives, not fake application data:

- Official v3 metadata retrieval and online downloads of all four artifacts, independently SHA-256 checked.
- Online-download/offline-Galaxy installation on Python 3.12.14, and offline installation from those archives on a freshly provisioned Python 3.14.7 virtual environment.
- `python --version`, `ansible --version`, `ansible-galaxy --version`, `pip check`, `pip show ansible-core resolvelib packaging`, `ansible-config dump`, and `ansible-galaxy collection list` on both interpreters.
- Only the selected collection root appears in config/list output. Sys.path collection scanning is disabled; no collection-path warning, KeyError or unsupported-controller warning occurred.
- All 18 existing/bundled/internal playbooks pass syntax checks on both interpreters. A localhost assertion actually executes `ansible.netcommon.vlan_parser` on both and returns the expected result without equipment access.
- Regression tests cover partial Galaxy cache, no-cache behavior, network retry classification/bounds, checksum/intercepted-body rejection, offline failure, private logging, pre-existing destination protection, runtime environment and a live worker's path stability across a release-pointer switch.
- Application regressions include existing job execution, scheduler, database migration, backup, role/permission enforcement, vendor command generation and simulated native deployment rollback. Live Gunicorn HTTP smoke passes on Python 3.14, including 12 pages, forced password change, API/assets, empty inventory and data/schedule persistence after restart.

## Remaining Risks

- The first failing HTTP response/cache history on the user's server is unavailable; the initial trigger cannot be claimed as diagnosed remotely. The new install route avoids the faulty API resolver/cache code path.
- A blocked artifact host still prevents an online install. Errors are bounded and actionable; verified offline artifacts provide an alternative. HTTPS verification remains enabled.
- Docker build/Compose startup and clean Ubuntu 24.04/26.04 apt/systemd/UFW installation were not executable here. Docker COPY/exclusion checks pass, the official base-image manifest digest was retrieved, and CI retains Docker/Windows gates. These do not substitute for host acceptance.
- Python 3.13 is officially supported and included in CI, but was not executed in this workspace. 3.12 and 3.14 were executed.
- The environment prohibits Unix sockets required by the full Ansible network_cli integration test. Cisco/RouterOS firmware operations and host-key behavior through that complete transport still require a lab. Localhost Ansible and the other loopback SSH tests pass.
- Version updates require deliberate lock/hash and compatibility review, including the existing exact-source known-hosts patch. Future Ubuntu/Python releases are not automatically certified by a numeric Ubuntu version check.

## Changes Applied

| File | Change |
|---|---|
| `requirements.txt` | Pin core 2.20.9 |
| `constraints.txt` | Same core pin; retain compatible resolvelib 1.2.1 and packaging 26.3 |
| `Playbooks/requirements.txt` | Delegate to the application requirements |
| `Playbooks/collections.lock.json` | New exact artifact URLs, SHA-256, dependency graph, core and controller minor policy |
| `tools/install_collections.py` | New checksum-locked installer, offline Galaxy, bounded downloads, atomic staging, diagnostics, preflight and read-only artifact option |
| `tools/ansible_environment.py` | New shared release path and executable selection; remove inherited global Ansible overrides |
| `tools/install_native.py` | Supported Python range; use shared installer; syntax-check before service replacement |
| `tools/run_native_linux.sh` | Reject unsupported Python; preserve explicit artifact directory across sudo |
| `tools/check_playbooks.py` | Use the same isolated runtime/config for all syntax checks |
| `tools/doctor_native.py` | Verify actual core, collection metadata/config and vendor/connection plugins |
| `app/services/automation.py` | Use this Python environment's Ansible and isolated per-job collection/config state |
| `app/api.py` | Report executable availability from the same Python environment as the worker |
| `ansible.cfg`, `Playbooks/ansible.cfg` | Disable sys.path collection scanning; retain SSH checks |
| `deploy/narsika.service` | Explicit config and disabled sys.path scanning alongside the existing collection path |
| `Dockerfile` | Pin Python patch/image digest; reuse shared artifact installer and runtime helper |
| `.dockerignore` | Include required new helper files in the build context |
| `.github/workflows/validate.yml` | Shared installer; Python 3.12/3.13/3.14 matrix |
| `tests/test_ansible_dependencies.py` | 25 regression cases for cache, isolation, pins, downloads and failure preservation |
| `README.md`, `Playbooks/README.md` | Correct controller range and collection installation commands |
| `docs/INSTALLATION.md`, `docs/ARCHITECTURE.md`, `docs/VALIDATION.md` | Current architecture, download/diagnostic guidance and validation pointer |
| `docs/ANSIBLE-DEPENDENCIES.md`, `docs/ANSIBLE-TEST-RESULTS.txt` | Diagnosis, full change register and current verification evidence |
| `MANIFEST.sha256` | Regenerated by the release packager |

`Playbooks/requirements.yml` was inspected and retained unchanged: all four collection versions already matched the selected supported dependency graph. No `pyproject.toml`, uv/Poetry/Pipfile lock or separate Ansible scheduler executable exists in this source; the existing scheduler delegates to the shared job runner.
