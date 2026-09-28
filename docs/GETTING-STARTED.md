# Getting started

This guide takes you from the first sign-in to a monitored device with a verified backup. It assumes Narsika is installed; if not, see [Installation](INSTALLATION.md).

## 1. Sign in and secure the admin account

Open the address printed by the installer, sign in as `admin` with the one-time password and choose a new password. Narsika stores only password hashes.

## 2. Create accounts for your team

Open **Settings → Users** and create one account per person. Narsika generates a temporary password that is shown **once**; the user must change it at first sign-in.

| Role | Can do |
|---|---|
| **ADMIN** | Everything, including users, settings, credentials, devices, Playbook uploads and high-risk firewall changes |
| **OPERATOR** | Monitoring, backups, running existing Playbooks, VLAN operations, their own scheduled tasks and scoped firewall changes |
| **VIEWER** | Read-only access to inventory, monitoring, backups, run history and the audit log |

Accounts are disabled rather than deleted, so their history stays attributable. Changing a user's role, disabling them or resetting their password signs out their existing sessions.

## 3. Add credential profiles

Open **Settings → Credentials**.

- **SSH profile** — username plus a password or private key (with optional passphrase); for Cisco, an optional enable password. The account needs the privileges your operations require: read-only is enough for health and backups, configuration rights for VLAN, firewall and changing Playbooks.
- **SNMPv3 profile** (optional) — username with SHA-256 authentication and AES-128 privacy (authPriv). Needed for interface counters and traffic rates.

Secrets are encrypted at rest and never shown again. To change a secret, enter a new one; leaving the field empty keeps the stored value.

## 4. Add a device

Open **Inventory → Add device** and enter a name, the management IPv4 address, the platform (Cisco or MikroTik), SSH port and, optionally, the SNMP port, group, model and notes. Assign the SSH profile and, if you have one, the SNMPv3 profile.

The device address must be inside the networks Narsika is allowed to manage (`NARSIKA_ALLOWED_NETWORKS`, private IPv4 ranges by default; see [Operations](OPERATIONS.md#configuration)).

**Prefer discovery?** Open **Discovery**, scan an allowed CIDR of up to 256 addresses, then **Verify** candidates: Narsika signs in with a credential profile and confirms the platform. Only verified candidates can be imported.

## 5. Trust the device's SSH host key

Narsika never trusts an unknown SSH key automatically. In **Inventory**, use the shield action (**Verify SSH host key**) to read the device's fingerprint. Compare it with a fingerprint you obtained through a path you trust — for example from a management host on the same trusted segment with `ssh-keyscan -p 22 <device-ip> | ssh-keygen -lf -`, or from your device records — and approve it only if they match.

If a trusted key later changes (for example after a device replacement), connections are refused until an administrator verifies the new key.

## 6. Check device health

Open **Device health** and select the device. Narsika collects reachability, identity, uptime, CPU, memory and platform details over SSH while the page is open. Anything the device does not report is shown as **N/A**.

**Interfaces** shows link state and, with an SNMPv3 profile, traffic rates. Rates appear after two valid counter samples.

Monitoring is on demand: nothing is polled in the background when no one is looking.

## 7. Take your first backup

Open **Backups** and capture a configuration from the device. Backups are encrypted, checksummed and attributed in the audit log. Select two backups and use **Compare selected** to see what changed; OPERATOR and ADMIN users can download them.

Take a backup before any change. Firewall changes take one automatically.

## 8. Run operations

- **Playbooks** — pick a Playbook for the device's platform, review its variables (JSON) and choose **Preview** (validate and show the plan, no configuration change) or **Apply configuration**. Runs are queued, shown live, and kept in **Recent runs** with their artifacts. See the [Playbook library](../Playbooks/README.md).
- **VLAN management** (Cisco IOS) — view VLANs and create, rename or remove one; saving to startup-config is optional.
- **Firewall** — reviewed Allow/Block rules on RouterOS and Cisco, including new Cisco ACLs. See [Firewall](FIREWALL.md).
- **Schedules** — run backups or Playbooks automatically. See [Scheduled tasks](SCHEDULES.md).

A run can be cancelled while it is queued or running, but cancelling does not undo commands the device already accepted.

## 9. Review the audit log

**Audit log** records sign-ins, administration and every network operation with its actor and result. Command output and secrets are never written to the audit log.

## Good habits

- Test every new operation on lab equipment first, with console or out-of-band access.
- Keep device clocks and Narsika's host clock in sync (NTP) so logs and schedules line up.
- Create a platform backup regularly with `sudo narsika-admin backup` and store it somewhere safe; it contains your encryption keys. See [Operations](OPERATIONS.md).
