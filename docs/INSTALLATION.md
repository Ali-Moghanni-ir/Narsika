# Installation and recovery — native release

## Ubuntu 24.04 or newer, x64

Desktop and Server are supported targets. systemd must be PID 1. Open a terminal in the extracted project and run:

```bash
bash run_linux.sh
```

The launcher uses the distribution's default Python 3 runtime when it is **3.12, 3.13 or 3.14**, the supported controller range for pinned `ansible-core==2.20.9`. Ubuntu 24.04 and 26.04 remain installation targets. A future distribution whose default Python falls outside that range is rejected until its runtime is validated; no unreviewed interpreter downgrade or PPA is added. Native Python patch updates remain managed by Ubuntu. The launcher installs missing Python/venv, OpenSSH client, ping, libssh, CA certificates, UFW and system utilities through Ubuntu repositories, establishes an administrative PATH and verifies prerequisites after apt completes. Python need not be installed before launch. Internet access is needed for apt, pinned pip requirements and checksum-locked Galaxy artifacts. Allow at least 2 GiB free installation space; 4 GiB RAM is a practical starting point, not a measured capacity guarantee.

A root/sudo installation creates a non-login system account without sudo/docker group membership. That account runs Gunicorn and Ansible, not the installer. New releases and virtual environments are root-owned; configuration is root:narsika 0640; persistent data is narsika-owned 0700. The service cannot write to application/configuration files.

Each install stages a uniquely named release. Existing code and data are never recursively replaced. A successful build switches /opt/narsika/current; application failure restores the former pointer/config where present. New dependencies are installed during deployment, not service startup. Staged/old releases remain for review; disk cleanup is not automatic.

### Package downloads and mirrors

The native installer defaults to `https://pypi.org/simple/`, timeout 120 seconds and three retries per download. A reset, DNS failure or unavailable pinned wheel is not fixed by increasing retries indefinitely. On a pip failure the terminal offers a different explicit HTTPS index for the **same staged release**, with at most three installation attempts in that invocation. Press Enter to stop. No constraints are relaxed and no index is selected automatically.

To use your already-tested mirror, substitute its real HTTPS index URL for the example below (the example domain is a placeholder):

```bash
NARSIKA_PIP_INDEX_URL=https://mirror.example.org/simple/ bash run_linux.sh
```

`NARSIKA_PIP_TIMEOUT` accepts 1–600 seconds; `NARSIKA_PIP_RETRIES` accepts 0–20. Existing `PIP_INDEX_URL`, `PIP_DEFAULT_TIMEOUT` and `PIP_RETRIES` are accepted as fallbacks. The launcher explicitly preserves these variables across its sudo step. The selected index and limits are recorded privately in the staged release's `dependency-source.json` after dependencies succeed.

TLS verification stays enabled. Index URLs containing credentials, query strings or fragments are rejected. Global/user pip configuration, extra indexes and `PIP_TRUSTED_HOST` are not inherited: one selected source is used consistently. Standard HTTP(S) proxy environment settings are preserved for native installation. Only choose a repository you trust; a mirror supplies executable dependencies. The `NARSIKA_PIP_*` settings also configure Docker Python downloads through the Linux/Windows launchers; they do not configure apt, Ansible Galaxy or base-image downloads.

To diagnose resolution without installing anything, run the following from this extracted project, substituting an existing staged virtual environment:

```bash
python3 tools/dependencies.py --python /opt/narsika/releases/RELEASE/.venv/bin/python --index-url https://mirror.example.org/simple/
```

This uses pip `--dry-run --ignore-installed`: metadata/downloads may occur but installed packages are not changed. Use the actual Python runtime of the intended deployment. For example, `SQLAlchemy==2.0.52` satisfies `SQLAlchemy>=2.0.16`; that pair alone is not a contradictory version requirement. Check index availability, full resolver output and Python compatibility before editing pins. Dependency consistency is checked with `pip check` after installation.

### Ansible collections

Ansible installation details, development setup, verified offline artifacts and diagnostics are in [Ansible dependencies](ANSIBLE-DEPENDENCIES.md). Native collections belong to `<release>/.venv/collections`; all required artifacts are pinned by version and SHA-256. Installation no longer resolves versions through the Galaxy API. Downloads have at most three attempts for transient failures; certificate, checksum and deterministic installation failures stop immediately. A private `<release>/.venv/ansible-install.log` retains diagnostic output. Collection/plugin verification and all bundled playbook syntax checks complete before stopping the previous service.

### HTTP, IP and firewall

Choose a port from 1024 to 65535, default 8000, and canonical management IPv4 CIDRs. No default Internet-wide scope is accepted. The service binds 0.0.0.0 on that port. It does not assign static IPs or alter DHCP, routing, DNS or interface configuration. Use the printed host addresses from a permitted management client.

The native installer uses `ufw prepend` for port-specific deny and trusted-source allow rules, without numeric insertion positions, resetting the firewall or restricting a single interface. It does not infer stored rules from `ufw status numbered`, which does not list them when UFW is inactive. The new service is activated only after the firewall commands succeed. A matching application source allowlist supplies a second boundary and ensures removed management sources remain blocked even when an old UFW allow rule is retained. Existing rules are not deleted. Port changes can leave an old Narsika port rule behind; review `sudo ufw status numbered` after changes.

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
sudo narsika-admin doctor
sudo narsika-admin logs
sudo narsika-admin restart
sudo narsika-admin backup
sudo narsika-admin upgrade /absolute/path/to/extracted/narsika
```

Manual backup pauses the service for a consistent SQLite snapshot plus uploads, encrypted backups/artifacts, known_hosts and encryption/session keys. The private archive is placed under /var/backups/narsika with mode 0600. A backup is offered, not required, before upgrades. No backup timer or retention schedule is installed. Preserve encryption keys; regenerating them makes existing encrypted data unreadable.

`doctor` reports release-pointer, key presence/permissions, database presence, service state, Python/pip consistency, Ansible availability, collection manifests and loopback HTTP health. It does not print key values, restart services, reset passwords, install packages or change firewall rules. A PASS is not a remote client or network-device acceptance test.

If first installation fails after bootstrap, the generated account and keys are retained for the retry. If service activation fails, newly created launch files are retained under that release's private `failed-deployment/` directory; previous launch files and the release pointer are restored where present. Failures in configuration/bootstrap/firewall/activation/health record their stage in `deployment-status.json`. Partial firewall changes are **not** rolled back automatically. If the database exists without its configuration, installation stops rather than generating replacement keys.

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

### Docker download speed and build cache

Use a current Docker Engine/Desktop with BuildKit. Docker retains unchanged dependency layers; a persistent BuildKit pip cache also reuses downloads when that layer must rebuild. The Ansible layer verifies artifact checksums, installs offline and applies the known-hosts patch together, so ordinary application changes reuse that layer. The Python 3.12.14 base image is pinned by registry digest. OS packages still come from Debian repositories during a clean build. Downloaded collection archives and Python package cache are not shipped in the resulting image. Temporary test directories and common runtime files are excluded from the build context.

For first installation with your trusted HTTPS mirror, replace the placeholder URL:

```bash
NARSIKA_PIP_INDEX_URL=https://mirror.example.org/simple/ bash run_linux.sh --docker
```

Windows PowerShell, from the extracted project:

```powershell
$env:NARSIKA_PIP_INDEX_URL = 'https://mirror.example.org/simple/'
.\run_windows.bat
```

Both Docker launchers pass `NARSIKA_PIP_INDEX_URL`, `NARSIKA_PIP_TIMEOUT` and `NARSIKA_PIP_RETRIES` as build arguments when provided. Defaults are official PyPI, 120 seconds and three retries. Docker accepts only the `NARSIKA_` variables here, not native-install `PIP_` aliases. Do not put credentials into build arguments. HTTPS URL validation and `pip check` use the same helper as native installation. There is no interactive mirror prompt inside Docker build: change the setting and rerun after a failure. Plain build progress identifies the step that stalls. A direct `docker compose build` does not inherit these custom environment variables; use the launcher or explicit `docker build --build-arg NARSIKA_PIP_INDEX_URL=https://mirror.example.org/simple/ -t narsika:local .`.

Once provisioned, normal startup needs neither build nor bootstrap:

```bash
docker compose up -d --no-build
```

Run setup again when installing updated source. Do not remove the Docker build cache between attempts. The first build on a fresh machine still downloads the base image, OS packages, Python packages and Galaxy collections. The pip mirror only affects Python downloads. No elapsed-time improvement or current image-build success is claimed without a Docker host benchmark. A future prebuilt versioned image could remove local builds from end-user installation, but no image has been published by this change.

Design reference: [Docker build-cache optimization](https://docs.docker.com/build/cache/optimize/).

## Existing installation

Do not copy a live database. The native installer refuses to silently ignore a legacy database in the extracted project's instance directory. See MIGRATION.md. Old encryption keys and uploaded/backup files must accompany encrypted databases; importing only SQLite is insufficient.

## Verification limits and references

Unit/integration and static checks are described in VALIDATION.md. Fresh Ubuntu package installation, actual UFW/systemd behavior, Windows UAC/reboot/WSL networking and this release's Docker build require a suitable test machine. Implemented support is not a claim those acceptance tests ran here.

Official references: [Ubuntu UFW](https://ubuntu.com/server/docs/how-to/security/firewalls/), [systemd execution sandbox](https://www.freedesktop.org/software/systemd/man/systemd.exec.html), [Docker Compose run options](https://docs.docker.com/reference/cli/docker/compose/run/), [Docker Ubuntu installation](https://docs.docker.com/engine/install/ubuntu/), [Docker Desktop Windows](https://docs.docker.com/desktop/setup/install/windows-install/), [WSL systemd](https://learn.microsoft.com/en-us/windows/wsl/wsl-config#systemd-support).
