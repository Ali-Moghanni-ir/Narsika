# Roadmap

This roadmap shows direction, not promises or dates. Priorities follow what makes Narsika safer and more useful on real networks. Ideas and feedback are welcome in [Issues](https://github.com/Ali-Moghanni-ir/Narsika/issues).

## Guiding principles

- **Real data only.** No demo records or invented measurements.
- **Safe by default.** Every device change is validated, queued, audited and, where possible, verified.
- **Humans approve changes.** This includes any future AI feature.
- **Simple to run.** One command to install, one small server to host it.

## Now — hardening the Public Beta

- **Lab verification** of Firewall and scheduled tasks on Cisco IOS / IOS XE and RouterOS 7, including RouterOS FastTrack and Cisco ACL creation.
- **Versioned releases**: Git tags and GitHub Releases with a packaged ZIP and SHA-256 checksums, so users no longer download a moving branch.
- **Prebuilt Docker image** published to a registry, removing the long first build on Windows and Docker hosts.
- **Browser end-to-end tests** and fresh product screenshots for the README.
- **Code readability**: automatic formatting and linting in CI, and splitting the core API module into smaller blueprints.

## Next

- **Platform driver layer** — each platform declares how to connect and what it supports (health, interfaces, backup, VLAN, firewall, Playbooks), replacing scattered vendor checks. This is the foundation for new device types.
- **Linux servers** — SSH health (CPU, memory, disk, uptime, services), configuration file backups and Playbooks, starting with Ubuntu and Debian.
- **HTTPS out of the box** — a certificate generated at install time or your own certificate, without a separate proxy.
- **Firewall improvements** — binding a Cisco ACL to an interface with lockout protection, and device-side confirmed changes that roll back automatically where the platform supports it.
- **Monitoring history and alerts** — background polling, metric history, alert rules and notifications (email, Telegram, webhook).
- **Log collection** — a syslog receiver with search and retention.

Monitoring history and log collection change the current "on-demand only" design and will be decided together with a storage review (SQLite limits versus an optional PostgreSQL backend).

## Later

- **AI assistant** — summarise and explain logs, explain configuration differences, help troubleshoot, draft Playbooks and answer questions about your inventory. Opt-in; secrets removed before anything is sent to a model; works with hosted and local models for air-gapped networks; it proposes, a human approves, and every action goes through the same queue, roles and audit log.
- **More platforms** — additional network vendors and Windows servers.
- **Topology view** built from real neighbour data.
- **Interface translations.**

## Not planned

- Multi-node clustering or high availability for the single-server edition.
- Features that require sending your network data to a third-party service by default.
