# Narsika Public Beta — native Linux release, 2026-09-10

This document describes the native Linux distribution included in the Narsika Public Beta. It complements the product overview in `README.md` and the installation guide in `docs/INSTALLATION.md`.

## Final integration

- Native platform detection accepts Ubuntu 24.04 and later x86_64 releases and uses the distribution-default Python when it is version 3.12 or newer. Fixed `python3.12` package and executable names were removed from native setup; Docker remains independently pinned.
- Native setup establishes a deterministic administrative PATH containing `/usr/sbin` and rechecks every required system command after apt. This prevents `runuser` and similar tools from disappearing when installation starts from a restricted sudo environment.
- The bootstrap subprocess previously received its own PATH without `/usr/sbin`, so parent-shell PATH changes alone could not fix `runuser` lookup. Its environment and the service unit now include administrative paths. A real `runuser --version` subprocess regression reproduces this lookup without switching users or changing the host.
- pip downloads now use a 120-second timeout and ten retries, retaining the configured index and HTTPS verification. This tolerates slow responses but cannot repair an unreachable package repository.
- Windows WSL mode reuses the newest installed explicitly versioned Ubuntu distribution at 24.04 or later, or installs Ubuntu 26.04 when none is present.
- The pinned runtime set resolves to 37 binary wheels for CPython 3.14 on compatible Linux x86_64 platform tags. Runtime and clean-host acceptance on Ubuntu 26.04 remain separate validation gates.
- The English product README includes native Ubuntu, Windows Docker and WSL2 installation, first sign-in, internal roles and operating commands. README.md is the only repository-root README; its local images are included in the release.
- The approved transparent metallic logo is used throughout the product. The 180-pixel asset serves the favicon and small navigation marks; the large sign-in artwork is preserved. The README banner is explicitly labeled a workspace illustration.
- Docker's build context now includes the bootstrap script required by its COPY instruction. A regression test checks the explicit Docker COPY inputs against the source and tool exclusions; this is not a substitute for a real image build.
- Failed monitoring refreshes cannot expose an expired cached result to concurrent callers. Failure is explicit and the next request can retry.
- RouterOS rule-equivalence checks include omitted protocol, source, destination and port selectors. A conflicting existing rule is left untouched and requires review, rather than being incorrectly reported unchanged.
- Native setup checks the SSH-listener and account-management tools it uses, adding iproute2, passwd and hostname to the missing-prerequisite installation path.
- An existing service account must have a dedicated non-privileged group. Validation does not modify interactive Ubuntu users or map them to Narsika roles.
- Simulated failed native upgrades restore the prior admin command and preserve prior service enablement, in addition to the existing code/configuration/unit recovery.
- Release integration has 123 passing automated tests and one environment-dependent skip. The live HTTP smoke test and all sixteen playbook syntax checks also pass; see VALIDATION.md for verification boundaries.

## Implemented

- Ubuntu 24.04-or-newer x64 native installation is the Linux default; the distribution's Python 3.12+ runtime is selected automatically and Docker remains explicit.
- Dedicated non-login account, versioned root-owned releases, private persistent data/config, systemd/Gunicorn service, staged upgrade and code/config recovery.
- Interactive port/management-CIDR input, all-IPv4-interface HTTP, scoped UFW additions and application source checks. No host IP reassignment.
- Shell-only random first password; no bootstrap secret in configuration or normal process environment. Existing-account preservation, forced change/confirmation and offline random reset.
- Random temporary user passwords from the admin UI; write-only response, mandatory next-login change, existing manual internal API input preserved for compatibility.
- ADMIN-only upload, OPERATOR/ADMIN execution, no playbook content sandbox. Malformed YAML rejected before replacing the active version.
- Built-in defaults refresh across releases; UI selects the newly active replacement and makes bundled Preview/Apply explicit.
- Atomic SQLite queue capacity reservation, bounded four-second reuse/coalescing of actual monitoring samples, bounded counter cache.
- Partial artifact collection produces an explicit failure while retaining accepted files; disabled RouterOS managed rules are not reported unchanged.
- Real server health indicator, actual role rendering, forced-change navigation fix, viewer artifact controls and selected relay logo. Previous logo file retained.
- Correlated request IDs and sanitized traceback locations without exception values/credential output.
- Refusal to open a database with a future schema version.
- Optional manual full-installation backup, admin recovery and upgrade commands; no timer or automatic retention.
- Windows Docker path retained and WSL2 Ubuntu entry added; test scripts adapted to terminal-only provisioning.

## Preservation and compatibility

No existing route, model, playbook or product capability was deleted. run_windows.bat and tools/run_native_linux.sh remain entry points. The previous Docker Linux launcher is preserved in tools/run_docker_linux.sh. Existing maintenance/snapshot/import/restore tools remain available; destructive maintenance is not enabled automatically.

No new Python runtime dependency or schema column/table was introduced by this change. Ubuntu deployment adds UFW and explicitly checks iproute2, passwd and hostname, using existing systemd. The existing additive legacy migration is unchanged except for future-schema rejection. Its encrypted pre-migration safety snapshot is distinct from the optional full-installation upgrade backup.

The transient Compose bootstrap service is a one-off use of the same application image and volume, not a new queue/database service. The bootstrap method intentionally changes the previously configurable initial password according to the owner's newest decision. Existing account passwords are preserved.

## Explicit limits

- No rewrite into another framework, horizontal scaling, HA, Topology or mobile/tablet work.
- Arbitrary admin playbooks can access the service account's files, keys and network; YAML validation is not isolation or a semantic guarantee.
- HTTP is intentionally unencrypted. UFW activation can affect existing host services and therefore prompts locally.
- New management scope is enforced by the app; old firewall rules/releases are retained, not automatically removed.
- A partly completed firewall update is not automatically undone on installer failure. Inspect UFW on the target host before retrying; service/configuration recovery is not a firewall transaction.
- An administrator replacing a playbook while a job is queued remains a deferred concurrency limitation. No upload isolation guarantee is claimed.
- Network commands may partially apply before failure/cancellation. No transaction rollback on switches/routers is claimed.
- Preview is input validation, not proof of successful device configuration. Device-specific correctness requires Cisco/RouterOS lab acceptance.
- Clean-host installation and current-image Docker/Windows acceptance have not been executed in this workspace.

See VALIDATION.md for evidence, INSTALLATION.md for commands and MIGRATION.md before moving existing data.
