# Running and maintaining Narsika

## Initial password lifecycle

Use `bash run_linux.sh` for native Ubuntu or `run_windows.bat` for Windows/Docker. See [installation](INSTALLATION.md). The offline provisioner generates a random password and prints it once in a terminal; normal Gunicorn startup cannot provision from an environment password. Existing accounts are never reset on restart.

Fresh installations have no bootstrap password to clean from configuration. The retained `configure.py --clear-bootstrap` command is only for old configurations. Never regenerate encryption/session keys.

Native operations use `sudo narsika-admin status|logs|restart|backup`. Recovery is `sudo narsika-admin reset-admin USERNAME`. The random reset password requires immediate change and revokes existing sessions. A full offline backup is manual/optional; no daily timer or retention is installed. Backup archives contain keys and must remain private.

To inspect or recover a full-installation archive, extract it into a new private offline directory using Python 3.12-or-newer tarfile's `filter='data'`, check its contents and preserve the matching application version. Never extract it directly over a running /etc or /var/lib tree. Restore into a separate installation and verify it before switching traffic; records created after the snapshot are not merged automatically.

## HTTPS on a shared server

Initial v1 access uses HTTP on the configured management networks. If you explicitly choose HTTPS later, first restrict the backend to 127.0.0.1:8000, install Nginx, provide a real DNS name and certificate, and adapt deploy/nginx.conf. Then set:

```text
NARSIKA_COOKIE_SECURE=true
NARSIKA_TRUST_PROXY_HOPS=1
```

Recreate the service, run `sudo nginx -t`, then reload Nginx through the host's normal service manager. The supplied proxy overwrites client forwarding headers. Enable proxy trust only when the backend is reachable solely through that proxy; direct local HTTP installations should keep it at 0. HTTPS responses include HSTS. The configuration does not request certificates, change DNS or expose a host on its own.

## Offline retention

No retention is enabled by installation. The existing offline tool is preserved for an administrator who explicitly chooses a cleanup policy; its --apply option deletes archived records after creating a verified archive. Start with preview and stop Narsika first. For a native installation:

```bash
sudo narsika-admin stop
sudo runuser -u narsika -- env NARSIKA_ENV_FILE=/etc/narsika/narsika.env /opt/narsika/current/.venv/bin/python /opt/narsika/current/tools/maintenance.py --days 90
# Add --apply only after reviewing and choosing that destructive cleanup.
sudo narsika-admin start
```

For the Docker volume:

```bash
docker compose stop narsika
docker compose run --rm --no-deps narsika python tools/maintenance.py --days 90
docker compose run --rm --no-deps narsika python tools/maintenance.py --days 90 --apply
docker compose up -d
```

Each pass handles up to 500 records per category. It selects old audit events, completed-run artifacts, already-archived backups and unreferenced completed runs. Active backups are retained. It writes and verifies an encrypted compressed archive under DATA_DIR/archives before deleting selected database rows/files. Copy verified archives to your protected backup storage; keeping every archive on the same disk will not solve disk-capacity limits. Installation rollback snapshots are never removed by retention.

Extract an archive for offline review or recovery without touching the live database:

```bash
.venv/bin/python tools/read_retention_archive.py instance/archives/ARCHIVE.json.gz.enc /new/private/recovery-directory
```

The output includes records.json and the original encrypted backup/artifact files. Retention extraction deliberately does not merge older records into an active database. A database restore should use a consistent installation snapshot and matching code/configuration.

## Container verification

On a Docker-equipped Linux host, `python3 tools/docker_smoke.py` builds the image and tests actual HTTP authentication, password change, empty installation, database writes and restart persistence using a dedicated disposable test volume. It never connects to a network device or touches the normal Narsika volume.

GitHub Actions runs this check and the Python/Ansible suite. Before deployment, check the actual workflow results. Before network writes, verify SSH trust, health and backup against the owner's Cisco/RouterOS lab and test one reversible operation for each firmware/model combination.
