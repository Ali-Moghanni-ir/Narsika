# Firewall Control — local review build

This is a complete Narsika source package for owner review, not a GitHub publication. Existing inventory, telemetry, credentials, backups, Playbook library, VLAN/ACL pages and installation paths are preserved. No production demo records are introduced.

## Start here

Use a separate lab VM and test routers first. Do not upgrade an active Narsika installation merely to inspect this preview.

### Ubuntu native

Extract the ZIP, open a terminal inside `narsika`, then:

```bash
bash run_linux.sh
```

The existing native installer handles supported Ubuntu 24.04+ environments and its prerequisites. Administrative privileges, package-repository connectivity and systemd are required for system installation. Complete the installer prompts, open its printed HTTP address and sign in with the generated first-use password. Complete the required password change. Use **Firewall control** in the sidebar, or `/firewall.html` at the same Narsika address.

### Windows

Use the existing `run_windows.bat` launcher for its Docker Desktop workflow, or `run_windows.bat -WSL` for Ubuntu through WSL2. Complete the relevant UAC, reboot and first-distribution setup prompts. Native Windows Python/Ansible execution is not the deployment model. See [Installation](INSTALLATION.md) for the full requirements and troubleshooting.

The new feature adds no Python package, npm build, background infrastructure service, host firewall rule, or Docker requirement to native Linux. Docker's existing `COPY app` includes the adapters automatically. Installer files are preserved; a fresh OS install and Docker image build were not run for this preview.

## Device prerequisites

1. Add a Cisco or MikroTik device to inventory with its real management IP and SSH port.
2. Assign an SSH credential profile with the required read/configure privileges.
3. Verify and trust the SSH host fingerprint using the existing device workflow. Host-key checking stays enabled.
4. Ensure the target is inside the server's configured allowed management networks.
5. Have an independent console/out-of-band path before testing disruptive rules.
6. Confirm the source IPv4 address the target sees for Narsika, including NAT. The displayed route hint cannot establish the post-NAT address.

## Workflow

1. **Select & inspect.** Select one device and choose **Read firewall**. Reads are on-demand; no response is fabricated if SSH fails.
2. **Choose traffic scope.** Specify source/destination IPv4 CIDRs or `any`. RouterOS supports input/forward/output and first/last placement. Cisco uses an existing named extended ACL and a free sequence number.
3. **Stage changes.** Use Allow/Block shortcuts for SSH, HTTPS, HTTP, UDP DNS, SNMP, Ping, Winbox or Telnet; Winbox is RouterOS-only. SSH uses the inventory SSH port. Custom rules support TCP/UDP single destination ports, ICMP echo requests or all IPv4 protocols. Edit, move or remove staged changes without contacting the device.
4. **Review.** The backend reads the device again, validates all intents and stores an encrypted immutable receipt for 10 minutes. Review the target, source IP, selectors, order, attachment points, warnings and exact generated commands.
5. **Save recovery instructions.** Download the review/recovery text and keep it outside Narsika. This is a manual recovery recipe, not a scheduled rollback.
6. **Confirm & Apply.** Five explicit acknowledgements are required by the API, not just the UI. Management-disrupting changes additionally require an administrator, confirmed OOB access, the exact device name and `DISCONNECT`. A potentially disrupting command must be last in execution order; the system will not silently reorder policy.
7. **Follow the receipt.** The worker rechecks account/target authorization and the firewall fingerprint, stores an encrypted backup, checks freshness again, executes the plan and reconnects independently to inspect the resulting configuration.

Staged changes are tab-local and are not silently restored across users. Changing targets or leaving with a draft warns about discarding it. A history receipt can be reopened after refresh or navigation. Repeated Apply on the same receipt returns the same operation instead of running again. Making a new review is a new intent, not an idempotent retry of the old review.

## Role behavior in this feature

| Role | Inspect / preview | Execute |
|---|---|---|
| Viewer | Yes; their own review history | No |
| Operator | Yes | Scoped standard-preset changes classified Medium |
| Admin | Yes; all review history | Custom and high-risk changes, including intentional lockout with extra confirmation |

Broad-source Allow, Block, custom-port presets and Cisco ACL edits are treated conservatively. The risk estimate is heuristic; it is not an exhaustive reachability analysis. These safeguards apply to this new workflow. Existing ACL and administrator-uploaded Playbook workflows are preserved, not silently restricted or transformed into a sandbox.

## Result meanings

| Status | Meaning |
|---|---|
| PENDING / QUEUED | Accepted by the queue; not proof of device change |
| APPLYING | Preflight and mandatory backup passed; execution is being attempted |
| VERIFYING | Reading a fresh device configuration after execution |
| SUCCESS | All requested entries and checked placement/preserved state verified; packet delivery is not tested |
| PARTIAL | Only part of the requested state matched, placement differed, or unexpected changes were observed |
| APPLIED_UNVERIFIED | Execution was attempted but a fresh SSH check failed; despite the status name, application of commands is **not proven** |
| FAILED | Preflight or execution/verification failed; consult the sanitized output |
| CANCELLED | Execution was stopped where possible; previously applied commands are not undone |
| INTERRUPTED | Worker restarted during execution; inspect the device before trying again |
| EXPIRED | An unused review exceeded its validity window; review again |

Polling failure in the browser does not cancel the operation. Cancelling is best effort and is not rollback. Operations are sequential device commands, not atomic multi-rule transactions. A late command can fail after an earlier one applied.

## Scope and deliberate limits

- **Additive IPv4 filtering only.** Existing rules are visible/read-only. This release does not toggle, delete or rewrite existing device rules, resequence whole ACLs, rebind interfaces, change services, or manage NAT, routing, IPv6, bridge firewall or Ubuntu UFW. Allow/Block adds a new matching rule; it is not a claim that a service became reachable.
- **Cisco:** existing named extended IPv4 ACLs only. Standard/numbered ACLs are not offered for modification. ACLs containing remarks are read-only because IOS may omit remark sequence numbers from its display. Running configuration changes are not automatically saved to startup. Review interface and VTY attachment points; other referencing features may not be discovered. Empty/unattached ACLs do not automatically become effective.
- **RouterOS:** the snapshot is exported IPv4 filter configuration, not every dynamically generated runtime rule. First-position rules are moved to index zero, so multiple first-position additions appear in reverse execution order. The review explicitly shows that order. Existing established/related, FastTrack, jump rules and final drops can affect behavior. A last-position Allow can be shadowed. The control does not simulate packet processing or flush existing connections.
- **No automatic timed rollback.** Backups and manual recovery instructions are available; a tested device-side scheduler/confirmed-commit mechanism is not implemented. Never depend on a disconnected server to recover its own management path.
- **No automatic retry after ambiguity.** Read and reconcile the target before preparing another review after an interruption, partial result or loss of access. A new receipt can intentionally add another rule; it does not deduplicate all semantically equivalent existing rules.
- **Concurrent external administrators:** local device locks and fingerprints protect Narsika operations, but cannot eliminate the final race with changes made outside Narsika. Verification reports uncertainty; it cannot guarantee isolation across every device management channel.
- **Desktop-first UI.** Uses the existing dark Narsika shell and local assets with a separate scoped stylesheet, native dialogs, keyboard-accessible tabs, labeled controls and live status text. Mobile/tablet support is not claimed.

## Suggested lab acceptance sequence

| Test | Expected observation |
|---|---|
| Empty inventory / missing credential / untrusted SSH key | Clear empty/error state, no fake rule list |
| Read RouterOS filter or supported Cisco ACL | Device output rendered and searchable; bindings shown where detected |
| Stage HTTPS Allow from a single management source | No device change before Apply |
| Edit port/scope; reorder; remove a staged rule | Only the local draft changes |
| Review then change device config outside Narsika | Apply fails freshness validation before sending change commands |
| Repeated Apply / simultaneous duplicate request | Exactly one operation for the receipt |
| Break backup creation | No firewall command sent |
| Submit incomplete acknowledgements directly to API | Rejected server-side |
| Preview as Viewer; attempt Apply | Preview works, Apply rejected |
| High-risk change as Operator | Rejected; Admin is required in this workflow |
| Apply a benign rule on each vendor | Encrypted backup exists; actual rule and order match receipt; end-to-end test performed separately |
| Disconnect during execution, using disposable lab and console | No false success; PARTIAL or APPLIED_UNVERIFIED where appropriate |
| Restart worker mid-job | INTERRUPTED; inspect current device config before retrying |
| Manually recover through console | Check current ownership/sequence and run downloaded recovery commands; verify independently |
| Desktop at 100% and 150% scaling | Inspect dialogs, table scroll, sidebar and confirmation controls |

## Optional isolated UI fixtures

For frontend review **without any router**, developers can explicitly start:

```bash
# From the extracted source, after installing requirements-dev.txt in a venv:
PYTHONPATH=. .venv/bin/python tests/ui_preview.py
```

Open `http://127.0.0.1:5187/ui-test-session`. This is a separate loopback-only disposable test server, clearly labeled **ISOLATED UI TEST FIXTURES**. It uses a temporary database, disables network connections and never starts the operation worker. Apply can only queue a test receipt; it never configures a device and does not fake a successful execution. Stop with Ctrl+C. Do not use this entrypoint, its test-session route or test identity for deployment, and do not forward its port to a network. Production entrypoints do not import it. This utility is included under `tests/`, not as an installation mode.

## Engineering notes

- New `firewall_review` table is created additively by existing `db.create_all()` under the startup migration lock. No existing column/table is dropped and schema-version compatibility remains at the existing version 2. Stop the application and preserve the database plus encryption key before upgrades.
- Previous code can ignore the extra table, but rolling back code does not reverse device configuration. Stop the worker and resolve pending firewall jobs before any downgrade. The previous worker cannot execute the new job kind. Preserve the extra table and operation receipts; do not drop it to roll back.
- Fixed internal playbooks live in `app/services/firewall_playbooks/`. The backend passes only validated server-built commands through protected temporary variables/inventory files. No growing user Playbook catalog is created by Apply. Temporary job material follows existing cleanup on completion/failure and restart; encrypted review receipts and audit remain.
- Offline retention does not purge operation records referenced by firewall receipts, preserving recovery history and foreign-key integrity. No automatic retention schedule is introduced.
- API endpoints are under `/api/firewall`: capabilities, device refresh, device reviews, review detail, review Apply and history. Authentication, role checks, CSRF, expiry, target binding, atomic receipt consumption and worker authorization are enforced server-side.

## References

Rule evaluation and chain behavior: [MikroTik Filter](https://help.mikrotik.com/docs/spaces/ROS/pages/48660574/Filter). Device-side recovery requires careful handling of platform/session behavior: [MikroTik Configuration Management](https://help.mikrotik.com/docs/spaces/ROS/pages/328155/Configuration+Management). Cisco configuration module options, including matching and save behavior: [Ansible cisco.ios.ios_config](https://docs.ansible.com/projects/ansible/latest/collections/cisco/ios/ios_config_module.html).
