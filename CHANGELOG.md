# Changelog

Notable changes to Narsika. Dates are in UTC. Versions follow [semantic versioning](https://semver.org/); each version has a Git tag `vX.Y.Z` and a [GitHub release](https://github.com/Ali-Moghanni-ir/Narsika/releases) with a ZIP archive and its SHA-256 checksum. Entries before 0.9.0 are listed by date.

## Unreleased

### Added
- **Playbook forms.** Every included Playbook that takes input now shows a form (labels, choices, examples) instead of a JSON editor: choose the device, fill the fields, Preview or Apply. The server checks types, ranges, choices and formats before a run is queued and drops unknown keys; the three Original playbooks, which have no checks of their own, get strict patterns. Playbooks without input say so; uploaded Playbooks keep the JSON editor.
- **Linux servers** as a third platform. Health (CPU, memory, load, filesystems, failed systemd units, uptime, kernel and distribution), interfaces with link state and one-second SSH rates, read-only configuration snapshots as backups, discovery hints for Ubuntu, Debian and Raspbian banners, and identity verification. Reads run fixed POSIX shell scripts without sudo and trust only verified host keys.
- Four Linux Playbooks: health report, pending package updates, ensure a package state, and manage a systemd service, with Preview/Apply and protection for SSH, network and core packages.
- Linux Playbooks run over OpenSSH with strict host key checking against Narsika's `known_hosts` only. Private keys, including passphrase-protected ones, are loaded into an `ssh-agent` that exists for one run.

### Changed
- A platform registry and driver modules (`app/services/platforms/`) now decide what each device type supports. VLAN and Firewall requests for a platform without that capability are refused with `422 UNSUPPORTED` before any connection.
- The interface reads platform names, vendors and capability filters from the server instead of hard-coded Cisco/MikroTik lists. The credential form explains that the enable password is the sudo password on Linux.

### Fixed
- Docker setup behind a proxy: the Windows and Linux launchers pass `HTTPS_PROXY`, `HTTP_PROXY` and `NO_PROXY` (on Windows also the system proxy) to the image build only. A proxy on `127.0.0.1` is reached through Docker Desktop's built-in proxy on Windows and through the host network on Linux. Nothing is stored in the image or the running container.
- Before the long build, a network check from the pinned base image stops setup within seconds and names the host it cannot reach (package index, package files, Ansible Galaxy or Debian).
- Failed Python package installs now tell DNS, proxy, TLS and connection problems apart from version-resolution errors, and the launchers repeat that one-line cause in their final error.
- Release packaging fails when a shipped file is missing from `MANIFEST.sha256`, and the Windows launcher warns about files left over from an older version in the same folder.
- Windows Docker setup no longer stops with `PermissionError: ... '/setup/.env'`: the configuration check now runs as the same user (root) that created the 0600 `.env`. The installer test asserts this.
- Both Docker launchers refuse to create new keys when a data volume with a database exists but the folder has no `.env`, and tell you to copy the old `.env` instead of making existing data unreadable.
- Linux Docker setup works with rootless Docker and Docker Desktop for Linux (setup containers run as container root there), on SELinux engines (`label=disable` on setup containers), and installs `docker-buildx` on Arch and openSUSE.
- `configure.py` reports unreadable or uncreatable `.env` files with one clear line instead of a traceback; the Linux launcher repeats it.
- The network check also tests the Ansible collection download host (`ansible-galaxy-ng.s3.dualstack.us-east-1.amazonaws.com`), and a proxy error page for the plain-HTTP Debian probe now counts as a failure.
- The container health check ignores proxy variables, so a runtime `HTTP_PROXY` cannot mark a working container unhealthy.
- Builds use `--provenance=false`, so a proxy URL passed as a build argument is not recorded in an attestation.
- `run_windows.bat -WSL` passes the Windows folder to `wsl --cd` directly (non-ASCII folder names work) and no longer blames systemd for every failure; Windows paths containing `[` or `]` work.
- The Linux launcher no longer treats a failed streamed step as successful when its last output line has no newline.

## 0.9.0 — 2026-10-19

Second public release, and the first with a version number (still Public Beta). It includes every change since the 2026-09-12 Public Beta. The full list is in [CHANGELOG.md](https://github.com/Ali-Moghanni-ir/Narsika/blob/main/CHANGELOG.md).

### Highlights since the Public Beta
- **Firewall**: read a device's IPv4 filter rules, stage Allow/Block changes, review the exact commands, take a mandatory backup, apply, and verify over a fresh SSH connection. Lockout risks are flagged before apply. Cisco named extended ACLs can be created; the separate Access lists page is merged into Firewall.
- **Scheduled tasks**: one-time, interval, daily and weekly Backup and Playbook runs with explicit time zones and per-device occurrence history.
- ansible-core 2.20.9 with checksum-locked collections; Python 3.12–3.14.
- Licensed under FSL-1.1-ALv2 (source-available).

### Added
- A version number: shown in the page footer, in **Settings → Connection** and in `GET /api/system`.
- Tagged releases: pushing a `vX.Y.Z` tag, or running the **Release Narsika** workflow on `main` from the Actions tab, tests the source and publishes a GitHub release with `narsika-X.Y.Z.zip` and a `.sha256` checksum.

### Security
- Playbook variables, scheduled-task variables and device credentials are passed to Ansible as data (`!unsafe`) and are never rendered as Jinja templates. Before this fix, an OPERATOR could run commands on the Narsika server by putting a Jinja expression in a Playbook variable, and a device password containing `{{`, `{%` or `{#` broke every Ansible job for that device.

### Fixed
- Configuration backups and Firewall reads no longer fail when a Cisco interface description or RouterOS comment contains words such as "syntax error" or "bad command name".
- Discovery recognises RouterOS devices, which announce themselves as `ROSSSH`, instead of labelling them `unknown`.
- SNMP interface rates are shown for devices that do not report the optional `ifCounterDiscontinuityTime` counter.
- A scheduled task that keeps failing with an unexpected error is disabled after five attempts with an attention message, instead of stopping every other schedule.
- In a multi-device scheduled task, a changed device fails only its own run, not the runs for the other devices.
- Cancelling a queued run before it starts now writes an output note and an audit event.
- A run picked up while the service is shutting down returns to the queue instead of staying `RUNNING` until restart.

### Removed
- The stale `MANIFEST.sha256` file from the repository. It is generated by `tools/package_release.py` and shipped inside each release archive.

## 2026-09-28

### Added
- **Firewall** workspace: read a device's IPv4 filter state, stage Allow/Block changes, review exact commands in an encrypted 10-minute receipt, take a mandatory backup, apply, and verify over a fresh SSH connection. New outcomes `PARTIAL`, `APPLIED_UNVERIFIED` and `EXPIRED`.
- Create new Cisco named extended ACLs from Firewall (unbound, low risk, removable with one recovery command).
- **Scheduled tasks**: one-time, interval, daily and weekly Backup and Playbook runs with explicit time zones and occurrence history.
- `narsika-admin doctor` read-only installation diagnostic.
- Configurable HTTPS package mirror, timeout and retries for native and Docker installs.
- License: FSL-1.1-ALv2 (source-available); `callback_plugins/` under GPL-3.0-or-later.
- New documentation: getting started, firewall, scheduled tasks, operations, roadmap, contributing and security policy; GitHub issue and pull request templates.

### Changed
- ansible-core 2.20.9 with checksum-locked collections installed without the Galaxy API; supported controller Python is 3.12–3.14; CI tests 3.12, 3.13 and 3.14.
- Operators can apply scoped firewall changes on Cisco (previously every Cisco change required an administrator). Block, `any` source, custom and lockout changes still need an administrator.
- The operation queue keeps per-device order, cancels queued work even when full, re-validates the requester's session and credentials before running, and reports worker liveness.
- Faster page loads (page-scoped bootstrap, summary run history) and UI fixes for duplicate submissions and stale responses.
- Documentation rewritten in English; earlier engineering reports moved to `docs/history/`.

### Removed
- The separate **Access lists** page. `/acl` and `/acl.html` redirect to Firewall; new `acl` runs and the legacy `POST /acl` form return `410 MOVED`. The original access-list Playbooks are administrator-only and cannot be scheduled.

## 2026-09-12 — Public Beta

First public release on GitHub.

- Inventory for Cisco IOS / IOS XE and MikroTik RouterOS with groups, SSH and SNMPv3 credential profiles and bounded discovery.
- On-demand health and interface monitoring; missing values shown as N/A.
- Encrypted configuration backups with comparison and download.
- Ansible Playbook library (5 Cisco, 5 MikroTik, 6 original) with preview/apply, admin uploads, VLAN and access-list operations, and a persistent operation queue.
- ADMIN, OPERATOR and VIEWER roles enforced on the server; random one-time initial passwords; CSRF, throttling and session revocation; audit log.
- Native Ubuntu installer with systemd, UFW scoping, staged releases and recovery; Windows installer using Docker Desktop or WSL2; Docker on Linux.
- Additive migration from the original *cisco-mikrotik-noc-panel* database, with encrypted snapshots.
