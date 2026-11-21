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

Download `narsika-X.Y.Z.zip` and `narsika-X.Y.Z.zip.sha256` from the [latest release](https://github.com/Ali-Moghanni-ir/Narsika/releases/latest), check the archive and extract it:

```bash
sha256sum -c narsika-0.9.0.zip.sha256
unzip narsika-0.9.0.zip
cd narsika
```

On Windows, compare the output of `Get-FileHash .\narsika-0.9.0.zip -Algorithm SHA256` (PowerShell) with the value in the `.sha256` file, then extract the ZIP completely.

Run the installer from inside the extracted folder. Use a fresh folder for each new version. The version is shown in the page footer and under **Settings → Connection**.

To try unreleased changes instead, clone the default branch (`git clone https://github.com/Ali-Moghanni-ir/Narsika.git`). It may contain work that has not been through a release.

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

### Docker network

Both Docker launchers (Windows and Linux) start Narsika on a dedicated bridge network, `198.18.250.0/24` with gateway `198.18.250.1`. The range is reserved for network benchmarking (RFC 2544) and is not used by enterprise LANs. Without it, Compose would take a subnet from Docker's default pools (`172.17.0.0/16` to `172.31.0.0/16`); when a managed device lives in an overlapping subnet such as `172.20.20.0/24`, the container routes its replies into the Docker bridge, and SSH fails with a timeout.

To use another range, set both values in `.env` and restart with `docker compose up -d --no-build`:

```bash
NARSIKA_DOCKER_SUBNET=198.18.251.0/24
NARSIKA_DOCKER_GATEWAY=198.18.251.1
```

Choose a range that no managed device, VPN or Docker network on the host uses. Docker Desktop's own internal network (`192.168.65.0/24` by default) is separate; change it in Docker Desktop only if a device lives in that exact range.

Docker publishes port 8000 through the gateway, so browsers on the Docker host, and on Docker Desktop every browser, reach Narsika from `198.18.250.1`. When `NARSIKA_WEB_NETWORKS` includes Docker's older bridge range `172.16.0.0/12`, as the default does, Narsika also accepts that gateway address, which keeps access working exactly as before. A narrowed list (for example `10.1.1.0/24` only) is not widened: add `198.18.250.1/32` to it explicitly if browsers must reach Narsika through Docker. Under Docker, the allow-list cannot tell those browsers apart, so restrict who can open Narsika with `NARSIKA_BIND_ADDRESS` or a firewall in front of the host.

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

## Proxy, VPN and filtered networks

The Docker image build downloads from four places: the Python package index (`pypi.org`) and its file host (`files.pythonhosted.org`), Ansible Galaxy (`galaxy.ansible.com`) and Debian (`deb.debian.org`). Before the long build, the Docker launchers run a short network check from a temporary container of the pinned base image. If a host cannot be reached, setup stops within seconds and names it, for example:

```text
Narsika setup stopped: Cannot reach files.pythonhosted.org (Python package files): DNS name resolution failed.
```

If the build itself fails later, the launcher repeats the one-line cause reported by the package or collection step instead of only "read the error above".

### How the launchers use a proxy

When `HTTPS_PROXY`, `HTTP_PROXY` or `NO_PROXY` are set — or, on Windows, when the Windows system proxy is enabled — the launcher passes them to the network check and the image build **only**, as Docker's predefined proxy build arguments. They are not stored in the image configuration or history, and they reach neither `.env` nor the running container. To confirm:

```bash
docker image inspect narsika:local --format '{{json .Config.Env}}'
```

A proxy on `127.0.0.1` or `localhost` belongs to your computer; inside a build container, `127.0.0.1` is the container itself:

- **Windows (Docker Desktop):** the launcher replaces a local proxy with Docker Desktop's built-in proxy, `http://http.docker.internal:3128`, which forwards to the proxy configured for Windows (Docker Desktop's default). To use another address, set `NARSIKA_BUILD_PROXY`, for example `http://host.docker.internal:10808` when your proxy also listens on non-loopback interfaces.
- **Linux:** the network check and the build run with `--network host`, so they can reach a proxy on the host.

SOCKS-only proxy settings are not passed to the build. Use the HTTP (or "mixed") port of your proxy client, such as v2rayN/xray, or a VPN.

Windows (PowerShell, in the project folder):

```powershell
$env:HTTPS_PROXY = 'http://127.0.0.1:10808'
$env:HTTP_PROXY = 'http://127.0.0.1:10808'
.\run_windows.bat
```

Linux:

```bash
HTTPS_PROXY=http://127.0.0.1:8080 HTTP_PROXY=http://127.0.0.1:8080 bash run_linux.sh --docker
```

### Other options

- **A VPN in TUN mode** that also handles DNS for Docker Desktop's WSL2 virtual machine needs no proxy settings at all.
- **Docker Desktop proxy settings** (Settings → Resources → Proxies) control how Docker Desktop itself pulls images.
- **A Docker client proxy configuration** in `~/.docker/config.json` (on Windows `%USERPROFILE%\.docker\config.json`) makes Docker add the proxy variables to every build *and every container it starts*, including the running Narsika container:

  ```json
  {
    "proxies": {
      "default": {
        "httpProxy": "http://http.docker.internal:3128",
        "httpsProxy": "http://http.docker.internal:3128",
        "noProxy": "localhost,127.0.0.1,.local,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"
      }
    }
  }
  ```

  Keep `localhost` and `127.0.0.1` in `noProxy` (the container's own health check uses them) and add every network that contains your devices, so traffic meant for your network is never sent to the proxy.
- **`NARSIKA_PIP_INDEX_URL`** with a trusted HTTPS mirror (see [Package downloads and mirrors](#package-downloads-and-mirrors)) fixes only the Python packages step. The Ansible collections still come from `galaxy.ansible.com` and the Debian packages from `deb.debian.org`, so those hosts still need a working path.

TLS verification, checksums and version pins stay enforced on every path. A proxy that intercepts HTTPS is reported as a certificate error; Narsika does not disable verification to work around it.

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
| Host key check: `SSH_TIMEOUT` or `NO_ROUTE` | Nothing answered on the SSH port. Check the device's firewall and the route back to Narsika; in Docker, check that the device subnet does not overlap the [Docker network](#docker-network). |
| Host key check: `SSH_REFUSED` | The port is closed: wrong SSH port, SSH service disabled, or a firewall rejects it. |
| Host key check: `SSH_HANDSHAKE_FAILED` | The device closed the connection before SSH started. On RouterOS check `/ip service` `address` (available-from) for SSH; on Linux check `AllowUsers`, `MaxStartups` or TCP wrappers. |
| Host key check: `NOT_SSH` | The port answers, but not with SSH. Check the port number. |
| Host key check: `SSH_ALGORITHM_UNSUPPORTED` | The device offers no host key or key exchange algorithm Narsika accepts. Narsika accepts Ed25519, ECDSA and RSA keys, including legacy `ssh-rsa`. Update the device's SSH settings or firmware. |
| Host key check: `LOCAL_NETWORK_DENIED` | The Narsika host or container is not permitted to open the connection (security policy, local firewall or container network mode). |

When an installation step fails, the installer restores the previous release and configuration where there was one, and records the failed stage in the new release's `deployment-status.json`. UFW rules added before the failure are **not** removed automatically; review them with `sudo ufw status numbered`.

## Moving an existing installation

Never copy a live database. To move data from an older Narsika or from the original panel, follow [Migration](MIGRATION.md). Encrypted databases need their original keys and data files, not just the SQLite file.

## References

[Ubuntu UFW](https://ubuntu.com/server/docs/how-to/security/firewalls/) · [Docker Engine on Ubuntu](https://docs.docker.com/engine/install/ubuntu/) · [Docker Desktop on Windows](https://docs.docker.com/desktop/setup/install/windows-install/) · [WSL systemd support](https://learn.microsoft.com/en-us/windows/wsl/wsl-config#systemd-support) · [Docker build cache](https://docs.docker.com/build/cache/optimize/)
