# Narsika documentation

## Using Narsika

| Guide | What it covers |
|---|---|
| [Installation](INSTALLATION.md) | Ubuntu, Windows, WSL2 and Docker installs, mirrors for slow downloads, first sign-in, troubleshooting |
| [Getting started](GETTING-STARTED.md) | Accounts and roles, credential profiles, your first device, SSH host keys, health, backups, Playbooks |
| [Firewall](FIREWALL.md) | Reviewed firewall changes on RouterOS and Cisco, new Cisco ACLs, risk levels, results and recovery |
| [Scheduled tasks](SCHEDULES.md) | Recurring backups and Playbooks, schedule types, statuses, permissions and limits |
| [Playbook library](../Playbooks/README.md) | The bundled Cisco and MikroTik Playbooks, their variables and command-line use |

## Running Narsika

| Guide | What it covers |
|---|---|
| [Operations](OPERATIONS.md) | Service commands, `doctor`, platform backup and restore, upgrades, configuration, HTTPS, data retention |
| [Migration](MIGRATION.md) | Moving an installation, importing the original panel's database, snapshots and rollback |

## Developing Narsika

| Guide | What it covers |
|---|---|
| [Architecture](ARCHITECTURE.md) | Process model, request guards, data model, queue, monitoring, Ansible, firewall pipeline, scheduler |
| [API](API.md) | The internal JSON API used by the web interface |
| [Ansible dependencies](ANSIBLE-DEPENDENCIES.md) | Pinned versions, checksum-locked collections, development setup |
| [Contributing](../CONTRIBUTING.md) | Development setup, tests, code style and pull requests |

## Project

- [Roadmap](ROADMAP.md) — what is planned next
- [Changelog](../CHANGELOG.md) — what changed and when
- [Security policy](../SECURITY.md) — how to report a vulnerability
- [History](history/README.md) — engineering reports from earlier releases, kept for traceability
