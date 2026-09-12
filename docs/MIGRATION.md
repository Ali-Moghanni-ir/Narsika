# Migration and rollback

## Fresh installation

The release contains no device database, telemetry or generated inventory. Run the native or Docker installer in INSTALLATION.md. configure.py generates private keys only; tools/bootstrap.py generates the initial random administrator password in a terminal. The initial administrator must change it. Preserve encryption keys.

## Moving an existing installation to native paths

Stop the old service and preserve its source/configuration/data. Do not install over a live database. Use a separate destination and keep the original until acceptance:

- For an original legacy SQLite database, copy it with `python3 tools/import_legacy_db.py /absolute/path/to/old.db --data-dir /var/lib/narsika` under sudo. This tool never overwrites an existing target.
- For an already encrypted Narsika installation, copy the complete stopped data directory (SQLite, backups/artifacts, uploads, known_hosts, archives and snapshots) to /var/lib/narsika. Copy the original private configuration to /etc/narsika/narsika.env and preserve both encryption/session keys. Set only its NARSIKA_DATA_DIR and NARSIKA_BACKUP_DIR to the new native locations if those keys exist.
- Run `bash run_linux.sh` from the newly extracted release. It creates/checks the service account, fixes ownership inside the native data tree while stopped, validates configuration and applies the existing additive migration. It preserves existing users and does not generate a new password for them.
- Verify users, inventory, trusted host keys and backup downloads before retiring the old installation. Do not run two copies against the same database or equipment.

The installer stops if the extracted project itself contains a legacy instance database while /var/lib/narsika is empty, preventing an unnoticed fresh product next to the real data. Archive/move that source outside the extracted release only after copying and verifying it; no source is automatically deleted.

## Importing an existing SQLite database

Stop the old application. Preserve its code and a consistent database/data backup. Create new private configuration and copy the database to a separate installation:

```bash
python3 tools/import_legacy_db.py /absolute/path/to/old.db --data-dir ./instance
```

The source is read-only and an existing target is never overwritten. For Docker, copy this prepared data into a new dedicated named volume or bind directory owned by UID/GID 10001 before starting the service. Do not mount the live original database directory.

Startup checks the existing encryption key and duplicate active IPs before altering schema or secret values. Duplicate IPs stop migration; resolve them in an offline copy. No device is silently merged or removed.

Migration creates and verifies narsika.db.before-v2.sqlite3.enc before changes. SQLite's backup API produces a consistent in-memory snapshot and Fernet authenticates/encrypts it before writing. Snapshot creation requires memory proportional to the database; allow additional memory for the encrypted copy. Existing users, IDs, groups and historical events are preserved. Bcrypt sign-in remains supported with mandatory password change.

Device passwords move into encrypted Credential profiles; a private encrypted record preserves original credential fields, including incomplete credentials. The old username/password columns are cleared. The old user password hash is cleared after it is copied to password_hash. Raw historical AuditLog.output is encrypted into encrypted_output, then cleared. No table or column is dropped.

An existing snapshot named narsika.db.before-v1.sqlite3 from the previous release is converted to a verified encrypted file before its plaintext file is removed. secure_delete, VACUUM and WAL truncation clear residual legacy content from the active SQLite database files. This cannot erase external backups, filesystem snapshots or copies in the original installation; manage those separately.

Migration is repeatable and records schema version 2 after cleanup. It refuses to start against an active Narsika worker. A partial failed migration retains the authenticated pre-migration snapshot for recovery; keep the same encryption key when retrying.

## Restoring a migration snapshot

Stop the new runtime and restore into a new offline path:

```bash
.venv/bin/python tools/restore_snapshot.py instance/narsika.db.before-v2.sqlite3.enc /new/private/old.db
```

The destination is never overwritten. The original encryption key must be loaded from .env or environment. Restore the old code and its matching database into an isolated old installation; never point old code at the extended live database. The restored file intentionally contains the original data, potentially including old plaintext secrets. New-version changes are preserved separately and are not automatically merged into the old schema.

## Upgrading and pending jobs

Stop traffic and finish or cancel active operations before replacing code. A full data/configuration backup is recommended and offered by the native upgrade command, but is optional according to the approved v1 policy. The legacy migration's authenticated safety snapshot is a separate existing protection. Never change the original encryption key. Built-in playbook paths and defaults refresh on startup; uploaded versions remain in the data directory.

RUNNING jobs become INTERRUPTED on process restart. Queued jobs record their target connection identity; an older pending job without that identity or a job whose target changed fails with TARGET_CHANGED and needs reviewed resubmission. Commands already sent are not rolled back by cancellation. Private abandoned job directories are cleaned after the worker lock is acquired.

For consistent SQLite snapshots use tools/snapshot.py, then protect the complete stopped installation (database, encrypted files, uploads, known_hosts and keys). See OPERATIONS.md for optional encrypted retention and HTTPS deployment.
