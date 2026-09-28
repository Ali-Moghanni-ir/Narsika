# Operations

Day-to-day running of a Narsika installation: service control, health checks, backups, upgrades, configuration, HTTPS and data retention.

## Service commands (native Ubuntu and WSL)

```bash
sudo narsika-admin status      # systemd status
sudo narsika-admin logs        # recent service logs
sudo narsika-admin start
sudo narsika-admin stop
sudo narsika-admin restart
sudo narsika-admin doctor      # read-only installation diagnostic
sudo narsika-admin backup      # full platform backup
sudo narsika-admin upgrade /absolute/path/to/extracted/narsika
sudo narsika-admin reset-admin USERNAME
```

`doctor` checks the active release, configuration and key permissions, the database, the service, Python packages, Ansible and its collections, and local HTTP health. It prints no secrets and changes nothing. A clean result does not test remote clients or network devices.

### Docker

```bash
docker compose up -d --no-build     # start
docker compose stop narsika         # stop
docker compose logs -f narsika      # logs
```

To install a new version, extract it next to the old one, copy the existing `.env` into it, and run the launcher again (`bash run_linux.sh --docker` or `run_windows.bat`); the named data volume is reused. Never run `docker compose down -v`.

## Platform backup

```bash
sudo narsika-admin backup
```

The service is paused briefly for a consistent snapshot. The archive is written to `/var/backups/narsika` with mode 0600 and contains:

- the SQLite database and pre-migration snapshots;
- private configuration **including the encryption and session keys**;
- uploaded Playbooks, encrypted device backups and run artifacts;
- trusted SSH host keys.

Treat the archive like a password vault: anyone who has it can decrypt your device credentials. Copy it to protected storage off the host. No backup timer is installed; schedule `narsika-admin backup` with your own tooling if you need regular copies.

### Restoring

Restoring is a manual, offline procedure — never extract an archive over a running `/etc/narsika` or `/var/lib/narsika`.

1. Extract the archive into a new private directory (with Python 3.12+ `tarfile`, use `filter='data'`) and check its contents.
2. Prepare a separate installation of the **same Narsika version** the backup came from.
3. With that installation stopped, place the configuration file in `/etc/narsika/narsika.env` and the data files in `/var/lib/narsika`, owned by the `narsika` account, then start it and verify users, devices, trusted host keys and backup downloads.
4. Switch users to the restored installation only after verification. Records created after the backup are not merged automatically.

## Upgrading

1. Finish or cancel running operations and pause scheduled tasks if you want a quiet window.
2. Extract the new version into a **new** folder.
3. Run:

   ```bash
   sudo narsika-admin upgrade /absolute/path/to/new/narsika
   ```

   The upgrade offers a platform backup first, builds and checks the new release, switches to it, and restores the previous release if the new one fails its health check.
4. Run `sudo narsika-admin doctor`.

Users, data, credentials, keys and trusted host keys are preserved. Database changes are additive; a verified encrypted snapshot is written before existing tables are changed. Queued runs whose target changed during the upgrade fail with `TARGET_CHANGED` and must be resubmitted after review. Runs that were executing when the service stopped become `INTERRUPTED`; check the device before re-running.

Old releases stay in `/opt/narsika/releases/` for rollback and are not cleaned up automatically.

## Configuration

Native installations read `/etc/narsika/narsika.env`; Docker reads the project's `.env`. Restart the service after editing.

| Variable | Default | Meaning |
|---|---|---|
| `NARSIKA_PORT` | `8000` | HTTP port. On native installs, change it by re-running the installer so UFW rules follow. |
| `NARSIKA_WEB_NETWORKS` | set by the installer | IPv4 CIDRs of clients allowed to open Narsika. Change through the installer on native installs so UFW matches. |
| `NARSIKA_ALLOWED_NETWORKS` | `10.0.0.0/8,172.16.0.0/12,192.168.0.0/16` | IPv4 ranges Narsika may manage and scan. Devices outside them are refused. |
| `NARSIKA_SCAN_MAX_HOSTS` | `256` | Maximum addresses per discovery scan (1–256). |
| `NARSIKA_JOB_TIMEOUT` | `300` | Maximum seconds for one Ansible run (5–300). |
| `NARSIKA_COOKIE_SECURE` | `false` | Set to `true` only behind HTTPS. |
| `NARSIKA_TRUST_PROXY_HOPS` | `0` | Set to `1` only when a single reverse proxy is the *only* way to reach Narsika. |
| `NARSIKA_BIND_ADDRESS` | `0.0.0.0` | Docker: host address the port is published on. |
| `NARSIKA_SECRET_KEY`, `NARSIKA_ENCRYPTION_KEY` | generated | Session and data encryption keys. **Never change or regenerate them.** |

Narsika runs one Gunicorn process with eight request threads and two operation workers per data directory. Do not add workers or run two instances against the same data.

## HTTPS with Nginx

Narsika serves HTTP, which is acceptable only on a trusted management network. For HTTPS, put an existing Nginx in front of it:

1. Make Narsika reachable only from the proxy (for example bind it to `127.0.0.1`), so clients cannot bypass Nginx.
2. Obtain a certificate for a real DNS name and adapt [`deploy/nginx.conf`](../deploy/nginx.conf).
3. Set in the configuration file:

   ```text
   NARSIKA_COOKIE_SECURE=true
   NARSIKA_TRUST_PROXY_HOPS=1
   ```

4. Restart Narsika, run `sudo nginx -t`, and reload Nginx.

The sample configuration overwrites client forwarding headers and sends HSTS. It does not request certificates, change DNS or open firewall ports for you.

## Data retention

Nothing is deleted automatically: audit events, run history, artifacts and backups accumulate until you clean them up. The offline maintenance tool previews first and deletes only with `--apply`, after writing and verifying an encrypted archive of everything it removes.

Native:

```bash
sudo narsika-admin stop
sudo runuser -u narsika -- env NARSIKA_ENV_FILE=/etc/narsika/narsika.env \
  /opt/narsika/current/.venv/bin/python /opt/narsika/current/tools/maintenance.py --days 90
# Review the preview, then repeat with --apply if you want to delete.
sudo narsika-admin start
```

Docker:

```bash
docker compose stop narsika
docker compose run --rm --no-deps narsika python tools/maintenance.py --days 90
docker compose run --rm --no-deps narsika python tools/maintenance.py --days 90 --apply
docker compose up -d --no-build
```

Each pass handles up to 500 records per category: old audit events, artifacts of finished runs, backups that were already archived in the UI, and finished runs nothing else refers to. Active backups, runs referenced by firewall receipts or schedule history, and installation snapshots are kept. Archives are written to `archives/` in the data directory; copy them to separate storage. To inspect one without touching the live database:

```bash
python tools/read_retention_archive.py /var/lib/narsika/archives/ARCHIVE.json.gz.enc /new/private/directory
```

## Moving to another server

Follow [Migration](MIGRATION.md). Always move the database together with its keys, encrypted files, uploads and `known_hosts`.
