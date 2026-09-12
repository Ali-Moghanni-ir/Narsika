# Installation and recovery — native release

## Ubuntu 24.04 or newer, x64

Desktop and Server are supported targets. systemd must be PID 1. Open a terminal in the extracted project and run:

```bash
bash run_linux.sh
```

The launcher uses the distribution's default Python 3 runtime and requires Python 3.12 or newer. It installs missing Python/venv, OpenSSH client, ping, libssh, CA certificates, UFW and system utilities through Ubuntu repositories. It establishes a standard administrative PATH and verifies every required command after apt completes. It does not require Python to be present before launch. Internet access is needed for apt, pinned pip requirements and Ansible Galaxy collections. Dependency downloads use an extended timeout and retry policy while retaining HTTPS verification and the configured Python package index. Allow at least 2 GiB free installation space; 4 GiB RAM is a practical starting point, not a measured capacity guarantee.

A root/sudo installation creates a non-login system account without sudo/docker group membership. That account runs Gunicorn and Ansible, not the installer. New releases and virtual environments are root-owned; configuration is root:narsika 0640; persistent data is narsika-owned 0700. The service cannot write to application/configuration files.

Each install stages a uniquely named release. Existing code and data are never recursively replaced. A successful build switches /opt/narsika/current; application failure restores the former pointer/config where present. New dependencies are installed during deployment, not service startup. Staged/old releases remain for review; disk cleanup is not automatic.

### HTTP, IP and firewall

Choose a port from 1024 to 65535, default 8000, and canonical management IPv4 CIDRs. No default Internet-wide scope is accepted. The service binds 0.0.0.0 on that port. It does not assign static IPs or alter DHCP, routing, DNS or interface configuration. Use the printed host addresses from a permitted management client.

The native installer inserts a port-specific deny and trusted-source allow rules into UFW, without resetting the firewall or restricting a single interface. A matching application source allowlist supplies a second boundary and ensures removed management sources remain blocked even when an old UFW allow rule is retained. Existing rules are not deleted. Port changes can leave an old Narsika port rule behind; review `sudo ufw status numbered` after changes.

If UFW is inactive, activation requires explicit terminal confirmation because existing UFW policies affect other applications too. The installer preserves the current SSH connection and detected sshd listening ports before enabling. Other services must be reviewed by the host administrator. This is not a firewall-policy migration tool.

HTTP transmits login/session traffic unencrypted. It is the approved v1 management-LAN mode, not safe public-Internet exposure. HTTPS remains optional through the existing proxy example. Never trust forwarded headers unless the configured reverse proxy is the only reachable frontend.

### First administrator

The offline bootstrap generates a cryptographically random password only in an interactive terminal, writes its hash directly to SQLite, and prints the password once. Do not record the terminal or redirect its output. No default admin/admin, environment-supplied password or bootstrap file is used for new installations.

Login forces password change and matching confirmation. Repeat installation preserves existing users/passwords. If you lose the initial password:

```bash
sudo narsika-admin reset-admin admin
```

This stops the service, generates a new temporary password for the existing administrator and restarts the service. Active sessions are revoked. OS sudo access is distinct from the platform ADMIN role; a Narsika operator cannot use this host recovery command.

### Operations

```bash
sudo narsika-admin status
sudo narsika-admin logs
sudo narsika-admin restart
sudo narsika-admin backup
sudo narsika-admin upgrade /absolute/path/to/extracted/narsika
```

Manual backup pauses the service for a consistent SQLite snapshot plus uploads, encrypted backups/artifacts, known_hosts and encryption/session keys. The private archive is placed under /var/backups/narsika with mode 0600. A backup is offered, not required, before upgrades. No backup timer or retention schedule is installed. Preserve encryption keys; regenerating them makes existing encrypted data unreadable.

## Windows

Run `run_windows.bat` on supported x64 Windows 10/11 with virtualization and WSL2 support. Windows Python, pip and winget are not required. Complete UAC, WSL update/reboot and Docker Desktop setup/license prompts when requested. The installer checks Docker's executable signature, builds the Linux image, provisions in a one-off terminal container with logging disabled, then starts the application and waits for health.

To choose Ubuntu in WSL instead:

```text
run_windows.bat -WSL
```

The launcher reuses an installed Ubuntu 24.04-or-newer distribution, preferring the newest supported explicit version. If none exists, it installs Ubuntu 26.04. Complete Ubuntu's first-launch Linux username/password setup, then rerun. If systemd is disabled, add `systemd=true` under the existing [boot] section of /etc/wsl.conf, then terminate only the selected Ubuntu distribution and reopen it. Do not replace unrelated WSL configuration.

WSL's private NAT/mirrored networking and the Windows firewall are separate from Ubuntu UFW. Local access usually uses localhost; LAN exposure requires an explicit Windows networking configuration. This installer does not silently create broad Windows forwarding rules.

## Optional Docker on Linux

```bash
bash run_linux.sh --docker
```

The Docker launcher keeps the former distribution-specific prerequisite installer. Python and Ansible run inside the image. Compose stores application data in its named narsika-data volume and configuration in the project .env. The transient bootstrap service uses the same image/volume, has no network and no log driver, and is not a continuously running infrastructure service.

Docker published ports may bypass host UFW routing. The application management-source allowlist remains active, but Docker/Windows forwarding can change the source address observed by the application. Validate access from a permitted and a denied client before team use; narrow host/firewall exposure explicitly.

For restart use `docker compose up -d`. For recovery, stop narsika, run `docker compose run --rm --no-deps bootstrap --reset-admin admin` in a terminal, then start it again. Keep the existing .env and volume. Do not use `down -v` for an ordinary shutdown.

## Existing installation

Do not copy a live database. The native installer refuses to silently ignore a legacy database in the extracted project's instance directory. See MIGRATION.md. Old encryption keys and uploaded/backup files must accompany encrypted databases; importing only SQLite is insufficient.

## Verification limits and references

Unit/integration and static checks are described in VALIDATION.md. Fresh Ubuntu package installation, actual UFW/systemd behavior, Windows UAC/reboot/WSL networking and this release's Docker build require a suitable test machine. Implemented support is not a claim those acceptance tests ran here.

Official references: [Ubuntu UFW](https://ubuntu.com/server/docs/how-to/security/firewalls/), [systemd execution sandbox](https://www.freedesktop.org/software/systemd/man/systemd.exec.html), [Docker Compose run options](https://docs.docker.com/reference/cli/docker/compose/run/), [Docker Ubuntu installation](https://docs.docker.com/engine/install/ubuntu/), [Docker Desktop Windows](https://docs.docker.com/desktop/setup/install/windows-install/), [WSL systemd](https://learn.microsoft.com/en-us/windows/wsl/wsl-config#systemd-support).
