# Repair release change register

The current 2026-09-08 native release is documented in RELEASE-NATIVE.md. The entries below describe earlier repairs and remain as history. Installation, bootstrap, source-access and logo decisions in the newer release document take precedence.

The owner authorized all necessary project repairs on 2026-09-07. Locked Flask/Jinja/custom CSS/Vanilla JS, SQLite and on-demand monitoring decisions remain in effect.

| Area | Change | Compatibility and recovery |
|---|---|---|
| Login | Atomic attempt reservation, release on success, account failure reset, expired-entry cleanup | Failed login limits remain 10/account and 50/IP per 15 minutes; valid repeated login no longer locks out users |
| Password forms | Accept both current/password/confirm and the three legacy field names | Existing route and legacy form preserved |
| Bootstrap | Provision before exec into Gunicorn; remove both password environment variables | Existing installations start without an initial password; configure.py --clear-bootstrap cleans host configuration |
| Migration | Validate key and duplicate inventory before writes; encrypted SQLite snapshot; encrypt old credentials and audit output, clear old plaintext fields | No column/table dropped; restore_snapshot.py restores the exact offline snapshot with the original key |
| Ansible | Collect artifacts on failure/cancellation; recheck scope at execution | Before-change backups survive later task failure; normal cancellation cannot reverse sent commands |
| Job queue | Save target connection identity; reject changes; remove abandoned private job directories after obtaining the worker lock | Old pending runs without identity require a reviewed resubmission |
| Monitoring | Invalidate health on credential changes and counter samples on target changes | Missing measurements remain null |
| Install | Restore prerequisite assistance and non-destructive venv recovery; validate supported config bounds | Distribution-default Python 3.12+ on Ubuntu 24.04+; Windows uses Docker or WSL2 |
| HTTPS | Optional Nginx host configuration and explicit one-hop proxy trust | Headers are ignored by default; no proxy service is automatically installed |
| Retention | Offline preview, explicit age/apply, encrypted archive before removal, extraction utility | Never runs automatically; active backups and active operations are retained |
| Validation | Regression suite, Docker build/HTTP/persistence smoke script, GitHub Actions workflow | Hardware/firmware compatibility remains a lab gate |

## File additions, replacements and exclusions

- Added tools/maintenance.py, tools/read_retention_archive.py, tools/restore_snapshot.py, tools/docker_smoke.py, tests/test_repairs.py, deploy/nginx.conf, docs/OPERATIONS.md and .github/workflows/validate.yml.
- Dockerfile is the canonical build filename; the lowercase duplicate is omitted for case-insensitive filesystems. inventory.ini contains empty groups and no sample credentials. ansible.cfg explicitly enables host-key verification.
- Updated application/services, runtime entry points, Dockerfile, Linux launcher, configuration generator, migration and test documentation. No new runtime dependency was needed in this repair pass.
- The old copied source folder reference/legacy and intermediate frontend-generation script are excluded from this release. Original source remains in Git history; the original workspace checkout is retained. Generated Python bytecode is removed from the integration branch.
- The eight old template files are replaced by the accepted pages under app/templates/pages plus shared layouts; all original route URLs and catalog identifiers remain. Authentication/CSRF and role enforcement are intentional security changes. GET logout displays a POST confirmation.
- AuditLog gains encrypted_output. Device username/password columns remain for schema compatibility but no longer contain legacy plaintext after migration. The old pre-v1 snapshot is converted to a verified .enc file. Snapshot restoration intentionally creates a private offline database that can contain old secrets.
- Existing UI archive actions remain reversible through retained records. Explicit offline retention can remove aged audit events, artifacts, already-archived backups and unreferenced completed runs only after encrypted preservation. Discovery runs and runs referenced by retained backups are not removed.

## Runtime limits

One Gunicorn process per SQLite volume; eight request threads, two operation threads, 256 addresses per discovery and a 300-second maximum Ansible timeout. Capacity must be measured on the actual target network. HA, permanent telemetry collection and unrelated infrastructure remain outside this release.

The original six playbooks now reside at Playbooks/Original. Linux startup and the Docker image provide the old lowercase playbooks directory as a symlink. The archive contains only the canonical tree, avoiding Playbooks/playbooks collisions on Windows/macOS. Explicit old Linux build scripts must use -f Dockerfile.

GitHub follow-up: the Docker smoke test now refreshes its dynamically mapped port after restart and emits container diagnostics on failure. The production image, authentication and persistent volume passed the corrected CI check; the full 62-test application suite also passed on Ubuntu, including network_cli.

## Self-installing launchers

- Default Linux launch now installs/reuses Docker and Compose, builds dependencies inside the image and waits for real health. Direct Linux execution is preserved as `--native`, with conditional prerequisite/pip installation.
- Windows now uses the built-in PowerShell launcher, installs missing WSL/Desktop prerequisites, verifies the Docker installer signature and needs no host Python or winget. Reboot/UAC/license requirements are visible. The legacy BAT name still delegates to one implementation.
- Added non-mutating configuration validation, UTF-8 BOM tolerance in native environment loading, portable script line endings and installer regression/CI checks. Existing secrets, data, routes, models and playbooks are preserved. No runtime Python dependency or database schema change was introduced.
- Linux default launch behavior changes from foreground native Gunicorn to background Compose; use `--native` to retain the direct execution route. Details and compatibility limits are in INSTALLATION.md.
