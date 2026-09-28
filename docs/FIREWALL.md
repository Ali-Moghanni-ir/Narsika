# Firewall

The **Firewall** workspace changes IPv4 filter rules on MikroTik RouterOS and Cisco IOS devices through one deliberate path: read the current state, stage changes, review the exact commands, take a mandatory backup, apply, and verify the result over a fresh SSH connection.

> Firewall is a **preview** feature. Its command generation and safeguards are covered by automated tests, but device behaviour still needs broader lab verification. Test on lab equipment with console or out-of-band access before using it on production devices.

## What it can and cannot do

| Can | Cannot |
|---|---|
| Add RouterOS `/ip firewall filter` rules at the **first** or **last** position of the input, forward or output chain | Edit, disable, reorder or delete existing rules |
| Add entries to an existing Cisco **named extended** IPv4 ACL at a free sequence number | Change standard or numbered ACLs, or ACLs that contain remarks (shown read-only) |
| Create a **new** Cisco named extended ACL | Bind an ACL to an interface or VTY line |
| Allow/Block presets for SSH, HTTPS, HTTP, DNS (UDP), SNMP, Ping, Winbox (RouterOS) and Telnet, or a custom TCP/UDP port, ICMP echo or any IPv4 protocol | NAT, routing, IPv6, bridge filtering, or the Ubuntu host's own UFW |
| Show a manual recovery recipe and keep an encrypted backup | Roll back automatically after a timeout |

An **Allow** rule only permits traffic; it does not start a service or prove that traffic will actually flow.

## Before you start

1. The device is in **Inventory** with its real management IP, SSH port and an SSH credential that can read and change the configuration.
2. The device's SSH host key has been verified (see [Getting started](GETTING-STARTED.md#5-trust-the-devices-ssh-host-key)).
3. You know which source address the device sees for Narsika. With NAT or a jump host this can differ from the address Narsika suggests.
4. For anything that could affect management access, you have console or out-of-band access to the device.

## Workflow

1. **Select and read.** Choose the device and click **Read firewall**. Narsika reads the current rules over SSH; nothing is invented if the read fails.
2. **Set the traffic scope.** Enter source and destination as an IPv4 address, CIDR or `any`.
   - *RouterOS:* choose the chain and whether new rules go **first** (before existing rules) or **last**.
   - *Cisco:* choose an existing named extended ACL and a free sequence number, or pick **+ Create new extended ACL** and type a name.
3. **Stage changes.** Use the service shortcuts or **Custom rule**. Staged changes live only in your browser tab; you can edit, reorder or remove them. Up to 20 changes per review.
4. **Review.** Confirm the source address, then click **Review change set**. Narsika reads the device again, validates every change and creates an encrypted review receipt that is valid for **10 minutes**. The review shows the target, final rule order, attachment points, risk level, warnings and the exact commands.
5. **Save the recovery instructions.** Download them and keep them outside Narsika.
6. **Apply.** Confirm the acknowledgements. Narsika then:
   - checks that your account and the device have not changed and that the firewall still matches what you reviewed;
   - saves an encrypted configuration backup — **if the backup fails, no command is sent**;
   - applies the changes in order and stops at the first failure;
   - reconnects over SSH and checks that every requested entry, and its placement, is present.
7. **Follow the result** on the page and in **History**. Reopening a receipt later shows its commands, recovery steps and outcome.

Applying the same receipt twice returns the same operation instead of running it again. A new review is a new change.

## Cisco: creating a new ACL

Choose **+ Create new extended ACL**, enter a name (a letter first, then letters, digits, `_` or `-`) and stage entries as usual; the first sequence defaults to 10.

- The name must not match **any** IPv4 access list already on the device, including standard and numbered lists.
- The new ACL is created **unbound**: it has no effect on traffic until you attach it to an interface (`ip access-group`) or a line (`access-class`). Binding is intentionally outside this workflow.
- Once bound, IOS denies all traffic that no entry permits (implicit deny). Plan the entries accordingly.
- The recovery recipe removes the whole new ACL (`no ip access-list extended NAME`).
- Running configuration is **not** saved to startup-config automatically.

## Risk levels and roles

Every change gets a risk level. The highest level in the change set decides who may apply it.

| Risk | Typical change | Who can apply |
|---|---|---|
| **LOW** | Entries in a new, unbound Cisco ACL | OPERATOR, ADMIN |
| **MEDIUM** | Allow a preset service from a specific source | OPERATOR, ADMIN |
| **HIGH** | Any Block, a source of `any`, or a preset with a non-standard port | ADMIN |
| **LOCKOUT** | A change that may cut Narsika's own SSH management path | ADMIN, with confirmed out-of-band access, the exact device name and the word `DISCONNECT` |

**Custom rules** always need an ADMIN, whatever their risk level. VIEWER users can read firewall state and prepare reviews to inspect them, but cannot apply. A change that may cause lockout must be the **last** item in the change set; Narsika will not reorder it for you. Risk levels are a conservative heuristic, not a full reachability analysis.

## Results

| Status | Meaning |
|---|---|
| **PENDING** | Queued. Nothing has been sent yet. |
| **APPLYING** / **VERIFYING** | Backup done; commands are being sent / the result is being read back. |
| **SUCCESS** | Every requested entry and its placement were verified, and existing rules are unchanged. Traffic itself is not tested. |
| **PARTIAL** | Only some entries matched, placement differed, or unexpected changes appeared. Inspect the device before retrying. |
| **APPLIED_UNVERIFIED** | Commands were attempted but Narsika could not reconnect to verify. This does **not** prove the commands were applied. Use console access and the recovery recipe. |
| **FAILED** | A pre-check or the execution failed. See the run output. |
| **CANCELLED** | Stopped where possible. Commands already accepted are not undone. |
| **INTERRUPTED** | The service restarted during execution. Inspect the device before trying again. |
| **EXPIRED** | The review was not applied within 10 minutes. Review again. |

Narsika never retries a firewall change automatically after an ambiguous result.

## Platform notes

**RouterOS**
- Several **first**-position rules end up in reverse order of insertion; the review shows the final order explicitly.
- Existing established/related, FastTrack, jump and final drop rules still affect traffic. A **last**-position Allow can be shadowed by an earlier drop.
- The snapshot is the exported filter configuration, not every dynamic runtime rule. Existing connections are not flushed.

**Cisco IOS**
- Only named extended IPv4 ACLs are editable. ACLs with remarks are read-only because IOS may hide the remarks' sequence numbers.
- Narsika shows interface and VTY attachment points it detects; other features that reference an ACL may not be detected.
- IOS does not store an ownership tag, so History matches entries to receipts by ACL, sequence and rule text.

## Recovery

- The downloaded recovery recipe lists the commands that remove what the review added, in reverse order.
- The encrypted pre-change backup is in **Backups**.
- There is **no automatic timed rollback**. If a change cuts management access, recover through the console or out-of-band path; a disconnected Narsika cannot repair its own access.

## Suggested lab test

| Test | Expected |
|---|---|
| Read firewall with no credential or an untrusted host key | A clear error, no rule list |
| Stage, edit, reorder and remove changes | Only the local draft changes |
| Review, then change the device configuration outside Narsika, then apply | Apply fails before sending commands |
| Apply a benign Allow on each platform | Backup exists; rule and order match the receipt |
| Create a new Cisco ACL, then bind it manually | Entries match; traffic follows the ACL only after binding |
| Try a HIGH change as OPERATOR | Rejected |
| Disconnect the device during apply (lab only, with console) | PARTIAL or APPLIED_UNVERIFIED, never SUCCESS |

## Previewing the interface without devices

Developers can open the Firewall page against disposable test fixtures, with all network access disabled:

```bash
PYTHONPATH=. .venv/bin/python tests/ui_preview.py
```

Then open `http://127.0.0.1:5187/ui-test-session`. This server is clearly labelled as test fixtures, listens on loopback only and never configures a device. Do not use it for deployment.

## References

[MikroTik filter rules](https://help.mikrotik.com/docs/spaces/ROS/pages/48660574/Filter) · [MikroTik configuration management](https://help.mikrotik.com/docs/spaces/ROS/pages/328155/Configuration+Management) · [Ansible `cisco.ios.ios_config`](https://docs.ansible.com/projects/ansible/latest/collections/cisco/ios/ios_config_module.html)
