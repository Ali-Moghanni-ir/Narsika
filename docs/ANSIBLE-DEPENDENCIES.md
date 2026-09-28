# Ansible dependencies

Narsika pins its Ansible runtime so that every installation — native, Docker and CI — runs the same code. This page lists the versions, explains how the collections are installed, and shows how to set up and diagnose a development environment.

The history of why the installer works this way (an `ansible-galaxy` cache bug and an unsupported Python/core combination) is recorded in [history/2026-09-14-ansible-dependency-correction.md](history/2026-09-14-ansible-dependency-correction.md).

## Versions

| Component | Selected version / policy |
|---|---|
| Native controller Python | Supported minor versions 3.12, 3.13, 3.14; reject <3.12 and >=3.15 |
| CI Python | 3.12, 3.13 and 3.14 |
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

The collection dependency closure is complete. Both its ranges and each collection's `meta/runtime.yml` are checked against the lock. Native Python patch versions follow Ubuntu security updates; they are recorded in the installation receipt rather than forcing an unrelated interpreter build. OS package repositories and existing Python wheel constraints are not content-addressed snapshots. The lock makes collection contents deterministic; it does not claim the entire operating-system image is bit-for-bit reproducible.

## How collections are installed

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

## Development setup and diagnostics

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

## Limits

- Online installation needs access to the official collection download host. If it is blocked, use verified offline artifacts (see above); downloads never fall back to another source.
- Only Python 3.12, 3.13 and 3.14 are supported controllers for ansible-core 2.20.9.
- Upgrading ansible-core or a collection requires updating `Playbooks/collections.lock.json` (versions and SHA-256) and reviewing `tools/patch_ansible.py`, which only patches the exact `ansible.netcommon` source it recognises.
- Syntax checks and plugin loading do not replace testing Cisco and RouterOS operations on real devices.
