# Changelog

Notable changes to Narsika. Dates are in UTC. Narsika does not publish version numbers yet; tagged releases are on the [roadmap](docs/ROADMAP.md).

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
