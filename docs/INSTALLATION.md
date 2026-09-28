# Installation

This guide covers every supported way to install Narsika, what the installers change on your machine, and how to recover when something goes wrong.

## Choose a method

| Method | Best for | Command |
|---|---|---|
| **Ubuntu native** (recommended) | A dedicated Ubuntu Server or Desktop, 24.04 or newer, x64 | `bash run_linux.sh` |
| **Windows + Docker Desktop** | A Windows 10/11 workstation | `run_windows.bat` |
| **Windows + WSL2 Ubuntu** | Windows, but you prefer the native Ubuntu service | `run_windows.bat -WSL` |
| **Docker on Linux** | Linux hosts where you already run containers | `bash run_linux.sh --docker` |

All methods run the same application. The native Ubuntu install is the primary, most tested path.

## Requirements

- **Ubuntu 24.04 or newer, x64**, with systemd, or **Windows 10/11 x64** with hardware virtualization enabled in firmware.
- A user with `sudo` (Linux) or administrator rights (Windows).
- Internet access during installation, for Ubuntu packages, Python packages and the Ansible collections. Slow or filtered connections are covered under [Package downloads and mirrors](#package-downloads-and-mirrors).
- At least **2 GiB free disk space**. About 4 GiB of RAM is a practical starting point.
- Network reachability from the Narsika host to your devices (SSH, and UDP 161 for SNMPv3).

You do **not** need to install Python, pip, Ansible, Gunicorn, SSH tools or UFW yourself.

## Get the source

Download the repository as a ZIP from GitHub (**Code → Download ZIP**) and extract it completely, or clone it:

```bash
git clone https://github.com/Ali-Moghanni-ir/Narsika.git
cd Narsika
```

Run the installer from inside the extracted folder. Use a fresh folder for each new version.

## Ubuntu (native)

```bash
bash run_linux.sh
```

### What the installer asks

1. **HTTP port** — default `8000`; any free port from 1024 to 65535.
2. **Management networks** — one or more IPv4 CIDRs (comma separated) whose clients may open Narsika, for example `192.168.10.0/24`. Internet-wide ranges are refused. Requests from anywhere else are rejected by UFW and again by the application.
3. **Enable UFW?** — only if UFW is currently inactive. Enabling UFW applies its policy to *every* service on the host, so review other listening services first and type `ENABLE` to continue. The installer keeps your current SSH access and the SSH ports it detects.
4. **Backup before upgrade?** — only when upgrading a running installation.

At the end the terminal shows the address, for example `http://192.168.10.20:8000`, and a **one-time password** for the `admin` account. Copy it now; it is never shown again or stored in plain text.

### What the installer does

- Installs missing Ubuntu packages (Python venv, OpenSSH client, ping, libssh, CA certificates, tzdata, UFW and system utilities).
- Uses Ubuntu's own Python when it is 3.12, 3.13 or 3.14. Other versions are refused rather than replaced.
- Creates a dedicated non-login system account, `narsika`, which runs the service and Ansible. It has no sudo or docker membership and cannot modify the application or its configuration.
- Builds each version as a separate release under `/opt/narsika/releases/` with its own Python environment and checksum-verified Ansible collections, syntax-checks every bundled Playbook, and only then switches `/opt/narsika/current` to it.
- Adds scoped UFW rules with `ufw prepend` for the chosen port. Existing rules are never deleted.
- Installs and starts the `narsika` systemd service and waits for a healthy response.

It does **not** change the host's IP address, DHCP, DNS, routes or network interfaces.

### Where things live

| Path | Contents |
|---|---|
| `/opt/narsika/current` | Active release (a symlink into `/opt/narsika/releases/`) |
| `/etc/narsika/narsika.env` | Configuration and encryption keys (root:narsika, 0640) |
| `/var/lib/narsika` | Database, encrypted backups and artifacts, uploaded Playbooks, trusted host keys (narsika, 0700) |
| `/var/backups/narsika` | Platform backups created with `narsika-admin backup` |

**Never delete `/etc/narsika` or `/var/lib/narsika`** to "fix" an installation: the encryption keys cannot be regenerated, and without them stored credentials and backups are unreadable.

Service management, backups and upgrades are covered in [Operations](OPERATIONS.md).

## Windows (Docker Desktop)

1. Extract the ZIP and open the folder.
2. Double-click **`run_windows.bat`** and approve the administrator prompts.
3. If the launcher installs WSL2 or Docker Desktop, complete their setup and license prompts and restart Windows when asked.
4. Run **`run_windows.bat`** again after the restart.

The launcher needs no Windows Python or winget. It verifies the Docker Desktop installer signature, builds the Narsika image, creates a persistent volume, generates the first administrator password in the terminal, starts the container and waits until it is healthy.

Local access normally uses `http://localhost:8000`. Exposing Narsika to other computers needs explicit Windows networking and firewall configuration; the launcher does not create broad forwarding rules.

## Windows (WSL2 Ubuntu)

```text
run_windows.bat -WSL
```

The launcher reuses the newest installed Ubuntu 24.04-or-newer distribution, or installs Ubuntu 26.04. Complete Ubuntu's first-launch username setup if asked, then run the command again; it performs the native Ubuntu installation inside WSL.

systemd must be enabled in WSL. If it is not, add `systemd=true` under the `[boot]` section of `/etc/wsl.conf` inside that distribution (keep the rest of the file), then restart only that distribution with `wsl --terminate <name>`.

WSL networking and the Windows firewall are separate from Ubuntu's UFW. From the same Windows machine, use `localhost`; reaching Narsika from the LAN requires Windows-side configuration.

## Docker on Linux

```bash
bash run_linux.sh --docker
```

The launcher installs Docker Engine and Compose if needed, builds the image, provisions the first administrator in a one-off container, and starts the service. Data lives in the `narsika-data` volume; configuration and keys are in the project's `.env` file.

After the first setup, start Narsika without rebuilding:

```bash
docker compose up -d --no-build
```

Keep `.env` and the volume. Never use `docker compose down -v` for a normal shutdown: it deletes your data.

Docker's published ports can bypass host UFW rules, and port forwarding can change the client address Narsika sees. Test access from an allowed and a denied client before team use.

## First sign-in

1. Open the printed address from a computer on an allowed management network.
2. Sign in as **`admin`** with the one-time password from the terminal.
3. Choose and confirm a new password.

Continue with [Getting started](GETTING-STARTED.md) to add credentials, your first device and a backup.

Lost the administrator password?

```bash
sudo narsika-admin reset-admin admin           # native Ubuntu or WSL
```

```bash
docker compose stop narsika                    # Docker
docker compose run --rm --no-deps bootstrap --reset-admin admin
docker compose up -d --no-build
```

A reset prints a new one-time password, forces a password change and signs out existing sessions.

## Package downloads and mirrors

Python packages come from `https://pypi.org/simple/` by default, with a 120-second timeout and three retries per download. If a download fails, the native installer offers to retry the same release with a different HTTPS package index (up to three attempts); press Enter to stop.

To use a mirror you already trust from the start, replace the example URL:

```bash
NARSIKA_PIP_INDEX_URL=https://mirror.example.org/simple/ bash run_linux.sh
```

Windows (PowerShell, in the project folder):

```powershell
$env:NARSIKA_PIP_INDEX_URL = 'https://mirror.example.org/simple/'
.\run_windows.bat
```

| Variable | Meaning |
|---|---|
| `NARSIKA_PIP_INDEX_URL` | HTTPS package index. URLs with credentials, query strings or fragments are rejected. |
| `NARSIKA_PIP_TIMEOUT` | Per-download timeout, 1–600 seconds (default 120). |
| `NARSIKA_PIP_RETRIES` | Retries per download, 0–20 (default 3). |

For native installs, `PIP_INDEX_URL`, `PIP_DEFAULT_TIMEOUT` and `PIP_RETRIES` are accepted as fallbacks. TLS verification always stays on, and version pins are never relaxed. A mirror only affects Python packages — not Ubuntu packages, Docker images or Ansible collections. Only use a mirror you trust: it supplies executable code.

To test whether dependencies resolve from a mirror without installing anything:

```bash
python3 tools/dependencies.py --python /opt/narsika/releases/RELEASE/.venv/bin/python \
  --index-url https://mirror.example.org/simple/
```

### Ansible collections without Galaxy access

The four Ansible collections are pinned by version and SHA-256 in `Playbooks/collections.lock.json` and installed without the Galaxy API. If `galaxy.ansible.com` is unreachable, download the four archives listed in the lock file on another machine, copy them to a folder, and point the installer at it:

```bash
NARSIKA_COLLECTION_ARTIFACT_DIR=/absolute/path/to/artifacts bash run_linux.sh
```

Every archive is still checksum-verified. Details: [Ansible dependencies](ANSIBLE-DEPENDENCIES.md).

## Docker download speed and build cache

The first Docker build downloads the base image, Debian packages, Python packages and Ansible collections. Later builds reuse unchanged layers and a persistent pip cache, so ordinary code updates are much faster. The Docker launchers pass `NARSIKA_PIP_INDEX_URL`, `NARSIKA_PIP_TIMEOUT` and `NARSIKA_PIP_RETRIES` to the build:

```bash
NARSIKA_PIP_INDEX_URL=https://mirror.example.org/simple/ bash run_linux.sh --docker
```

A plain `docker compose build` does not pick these up; use the launcher or `docker build --build-arg NARSIKA_PIP_INDEX_URL=... -t narsika:local .`. Don't clear the build cache between attempts. When you install a new version, run the launcher again so the image is rebuilt.

## Troubleshooting

Start with the built-in diagnostic on native installs. It checks the release pointer, configuration and key permissions, database, service state, Python packages, Ansible and its collections, and local HTTP health. It prints no secrets and changes nothing.

```bash
sudo narsika-admin doctor
sudo narsika-admin logs
```

| Symptom | What to do |
|---|---|
| A download timed out or failed | Rerun from a fresh extraction, optionally with a [mirror](#package-downloads-and-mirrors). Existing data, accounts and keys are kept. |
| "The selected port is already occupied" | Choose another port, or stop the program using it. |
| "Firewall activation not confirmed" | You did not type `ENABLE`. Review UFW, then rerun. |
| The page does not open from another computer | Check that the client is inside a management network you entered, and review `sudo ufw status numbered`. |
| "A local legacy database exists" | An old `instance/` database is inside the extracted folder. Follow [Migration](MIGRATION.md). |
| "Python 3.12, 3.13 or 3.14 … is required" | Your Ubuntu's default Python is outside 3.12–3.14, or a required package could not be installed. Use a supported Ubuntu release and check `apt` access. |
| WSL install stops at systemd | Enable systemd as described in [Windows (WSL2 Ubuntu)](#windows-wsl2-ubuntu). |

When an installation step fails, the installer restores the previous release and configuration where there was one, and records the failed stage in the new release's `deployment-status.json`. UFW rules added before the failure are **not** removed automatically; review them with `sudo ufw status numbered`.

## Moving an existing installation

Never copy a live database. To move data from an older Narsika or from the original panel, follow [Migration](MIGRATION.md). Encrypted databases need their original keys and data files, not just the SQLite file.

## References

[Ubuntu UFW](https://ubuntu.com/server/docs/how-to/security/firewalls/) · [Docker Engine on Ubuntu](https://docs.docker.com/engine/install/ubuntu/) · [Docker Desktop on Windows](https://docs.docker.com/desktop/setup/install/windows-install/) · [WSL systemd support](https://learn.microsoft.com/en-us/windows/wsl/wsl-config#systemd-support) · [Docker build cache](https://docs.docker.com/build/cache/optimize/)
