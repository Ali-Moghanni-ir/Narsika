# Verification report — Narsika Public Beta, 2026-09-10

Source: the Narsika Public Beta source published on the `main` branch. The evidence below records the application, packaging and installer verification completed for this release.

| Check | Result | Evidence and boundary |
|---|---|---|
| Full Python application/integration suite | PASS | 123 passed, 1 skipped in 39.69 seconds; Python 3.12, real Flask/SQLite/Ansible dependencies |
| Bootstrap runuser lookup | PASS | Actual runuser --version execution under the bootstrap environment, with a restricted parent PATH; no user switch or privileged host change |
| Native-release and integration regressions | PASS | Included in the full suite: Ubuntu version gate, adaptive Python selection, terminal bootstrap, simulated failed-upgrade recovery, concurrent sample failure and packaged brand assets |
| CPython 3.14 dependency resolution | PASS | All 37 pinned runtime wheels resolved for compatible manylinux x86_64 tags, including ansible-pylibssh, cffi, PyYAML and greenlet; this is not a runtime test |
| Ubuntu version gate | PASS | Isolated tests accept 24.04, 24.10, 26.04 and later numeric versions; reject pre-24.04, malformed versions, other distributions and non-x86_64 native targets |
| Random bootstrap / repeat installation | PASS | Actual pseudo-terminal subprocess; random password, hash-only DB, forced change, existing-account preservation, rejection of noninteractive first bootstrap |
| Admin-created/reset user passwords | PASS | One-time response, role restrictions, forced confirmation and session revocation |
| Management source allowlist | PASS | Permitted/denied client tests; untrusted X-Forwarded-For cannot bypass it |
| Native upgrade failure recovery | PASS, simulated | Health/UFW failure injection with previously enabled/disabled services; pointer/config/unit/admin command and enablement restored, data retained; no host package/service/firewall changes performed |
| Dedicated service account | PASS, simulated | Rejects shared/privileged groups and login accounts without modifying Ubuntu users; calling username does not determine product access |
| Queue and monitoring | PASS | Capacity limit, shared actual sample, bounded cache/credential identity, failed refresh cannot release expired data, existing worker/cancellation/restart tests |
| RouterOS existing-rule comparison | PASS, generation only | Missing selectors and disabled state are explicitly compared; actual firmware behavior requires lab acceptance |
| Playbook upload and artifacts | PASS | Malformed YAML rejected, trusted tasks accepted, permission checks, artifact limits reported without losing accepted files |
| Real Gunicorn HTTP | PASS | Random provisioning, forced login/password change, ten product pages, selected logo, empty inventory, database write and restart persistence |
| Secret-free service logs | PASS | Smoke-test passwords absent from captured Gunicorn logs |
| Original application regressions | PASS | Migration, encrypted backups, CSRF, login throttling, revoked sessions, templates, legacy routes, real loopback SSH and localhost Ansible |
| Full Ansible network_cli transport | SKIPPED locally | Authoring environment prohibits required Unix-domain sockets; no new-release hardware success claimed |
| Ten requested and six preserved playbooks | PASS | All sixteen passed ansible-playbook --syntax-check using installed pinned collections; no device write |
| Python / JavaScript / shell syntax | PASS | AST checks, Node parsing of all eleven runtime JS modules, bash -n for the three launchers and shared platform helper |
| Package structure and Compose persistence | PASS | Explicit release-file whitelist, case-insensitive path checks, YAML structure and named data volume |
| Docker build-context inputs | PASS, static | Every explicit COPY source exists; bootstrap.py is no longer excluded by .dockerignore. No image build was performed |
| Final README and local brand assets | PASS | Referenced images are packaged, RGBA logos have expected 1024/180-pixel sizes, shell serves the small asset, original six and requested ten playbooks remain present |
| Configuration / Gunicorn factory load | PASS | Secret-key generation, no password configuration, repeat preservation and gunicorn --check-config |
| Fresh Ubuntu apt/venv/systemd/UFW installation | NOT RUN | Requires disposable Ubuntu 24.04 and 26.04 x64 hosts with sudo and real systemd/UFW. This sandbox did not perform privileged changes |
| systemd-analyze verify | BLOCKED here | Host verification could not establish the required working directory; unit behavior still requires target-host acceptance |
| Current Docker image and Compose bootstrap | NOT RUN | Docker unavailable in this workspace; updated smoke/CI scripts included |
| Current Windows PowerShell / Docker / WSL installer | NOT RUN | No Windows runtime here. Complete UAC/reboot, first Ubuntu user and network acceptance on a Windows test PC |
| Browser interaction / visual regression | NOT RUN | Templates, assets and JS syntax checked; no full browser interaction claim |
| Real Cisco IOS / RouterOS / SNMPv3 firmware | LAB_REQUIRED | Model/firmware-specific commands, backups, configured changes, firewall order and routing require the owner's lab |

Commands used with the existing isolated Python environment and pinned Ansible collections:

```bash
python -m pytest -q -p no:cacheprovider
python -m pytest -q -p no:cacheprovider tests/test_native_release.py tests/test_release_integration.py
python tools/check_playbooks.py
python tools/validate_package.py
python tools/http_smoke.py
```

No real switch/router was modified. Test databases, secrets, virtual environments, caches and runtime output are excluded from the release. Test fixture records are created only in disposable test databases; they are not product seed data.

## Required host acceptance

1. On disposable Ubuntu 24.04 and 26.04 x64 machines, install from a fresh extraction without Python dependencies preinstalled.
2. Confirm the system account, file permissions, service restart after reboot and preservation on a second install.
3. Test HTTP from an allowed management source and a denied source on every intended IPv4 interface; check existing SSH and other host services before/after UFW activation.
4. Test first login, reset-admin and optional backup/upgrade with real systemd. Confirm generated passwords are absent from journal/container logs and configuration.
5. On Windows, validate Docker Desktop installation/reboot and the optional WSL Ubuntu path. Check observed source addresses before LAN exposure.
6. In a Cisco/RouterOS lab, verify fingerprint, health, backups, one reversible managed operation and every bundled configuration playbook on each supported model/firmware.

## Historical evidence

docs/TEST-RESULTS.txt retains the previous authorized publication's CI evidence. Its PASS results do not certify the new native installer, revised bootstrap or current image.
