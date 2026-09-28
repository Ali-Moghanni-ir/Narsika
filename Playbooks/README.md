# Narsika Playbook library

This folder holds the Ansible Playbooks that Narsika ships and lists in **Playbooks**: five for Cisco, five for MikroTik, and the six original Playbooks from the first version of the project. You can run them from the web interface or directly with `ansible-playbook`.

## Bundled Playbooks

| File | Purpose | Effect on the device |
|---|---|---|
| `Cisco/01_health_report.yml` | Version, IP addresses, CPU and interfaces | Read-only; JSON report on the controller |
| `Cisco/02_backup_running_config.yml` | Full running-config backup | Read-only; private local file |
| `Cisco/03_ensure_vlan.yml` | Create a VLAN or correct its name | Only the selected VLAN; port membership unchanged |
| `Cisco/04_configure_access_port.yml` | Access VLAN and description on one port | Checks L2 capability and that the VLAN exists; refuses to convert a trunk unless allowed |
| `Cisco/05_ntp_syslog.yml` | Add an NTP server and a syslog destination | Existing destinations are kept |
| `MikroTik/01_health_report.yml` | System resources, identity, interfaces and addresses | Read-only; works on CHR |
| `MikroTik/02_export_config.yml` | RouterOS 7 text export | Read-only; sensitive fields hidden by default |
| `MikroTik/03_ensure_vlan_interface.yml` | VLAN interface on an existing parent | Created if missing; stops on conflict; bridge filtering unchanged |
| `MikroTik/04_dhcp_reservation.yml` | Reserve an IP for a MAC on an existing DHCP server | Checks for conflicts; dynamic leases are not converted |
| `MikroTik/05_static_route.yml` | Named route in the `main` table | Checks existing routes; a default route needs explicit permission |

`Original/` keeps the six Playbooks of the original project; they appear in the catalog with an *Original* prefix. The two original access-list Playbooks (`cisco_acl.yml`, `mikrotik_acl.yml`) can be run only by an administrator and cannot be scheduled — use [Firewall](../docs/FIREWALL.md) for filter changes.

Example variables for every Playbook are in `examples/<vendor>/`.

## Preview and apply

The Playbooks that change configuration take a boolean `narsika_apply`, which defaults to `false`:

- **`false` (Preview)** — inputs are validated and the plan is shown; the device configuration is not changed.
- **`true` (Apply)** — the change is made.

In the web interface, **Operation mode** sets `narsika_apply` for you. Changing Playbooks run one host at a time (`serial: 1`) and stop at the first error. This is not a transaction: there is no automatic rollback.

## Requirements

- Cisco IOS or IOS XE. VLAN and access-port Playbooks need a switch with L2 features, not just any Cisco router. Check preflight output against your IOS version.
- MikroTik Playbooks target **RouterOS 7**; the major version is checked before export or change. RouterOS 6 is not supported.
- An SSH account with suitable privileges, and a verified host key.

## Running from the command line

From the project root, with Python 3.12, 3.13 or 3.14:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python tools/install_collections.py
export ANSIBLE_COLLECTIONS_PATH="$PWD/.venv/collections"
export ANSIBLE_COLLECTIONS_SCAN_SYS_PATH=False
python tools/install_collections.py --verify-only
cd Playbooks
```

Copy `inventory.example.yml` for your lab and set real addresses and usernames. The `192.0.2.0/24` and `198.51.100.0/24` addresses in the examples are documentation ranges. Supply passwords with `--ask-pass` and, for Cisco enable, `--ask-become-pass`, or use Ansible Vault — never put secrets in `--extra-vars` or on the command line. Host-key checking is enabled in `ansible.cfg`.

### Read-only examples

```sh
ansible-playbook -i inventory.lab.yml Cisco/01_health_report.yml --limit cisco_lab --ask-pass --ask-become-pass
ansible-playbook -i inventory.lab.yml Cisco/02_backup_running_config.yml --limit cisco_lab --ask-pass --ask-become-pass
ansible-playbook -i inventory.lab.yml MikroTik/01_health_report.yml --limit mikrotik_lab --ask-pass
ansible-playbook -i inventory.lab.yml MikroTik/02_export_config.yml --limit mikrotik_lab --ask-pass
```

Reports and backups are written on the **controller** in a unique `artifacts/host_<name>_<random>/` folder (mode 0700, files 0600); change the location with `narsika_artifact_root`. Collectors refuse `--check`, because they must actually read the device.

A full Cisco configuration can contain secrets; it is hidden from task output with `no_log`. The MikroTik text export is not a binary backup and does not include passwords, certificates or SSH keys. A partial export is reported as a failure.

### Change examples

```sh
cp examples/Cisco/03_ensure_vlan.vars.yml lab-vlan.yml
ansible-playbook -i inventory.lab.yml Cisco/03_ensure_vlan.yml --limit cisco_lab -e @lab-vlan.yml --ask-pass --ask-become-pass
```

With `narsika_apply: false` this validates and prints the plan. After reviewing it, set `narsika_apply: true` (a boolean) in your copy and run again. MikroTik works the same way, for example with `examples/MikroTik/04_dhcp_reservation.vars.yml`.

`--check` on changing Playbooks only validates variables and shows the plan; it is not a full device dry-run. The RouterOS command module does not really support check mode.

## Important variables

| Variable | Meaning |
|---|---|
| `narsika_targets` | Target group; defaults to the vendor group (`all` in single-host runs from Narsika) |
| `narsika_apply` | Boolean; `false` by default for the changing Playbooks |
| `narsika_artifact_root` | Local folder for reports and backups on the controller |
| `save_config` | Cisco: also save the change to startup-config (default `false`) |
| `vlan_id`, `vlan_name` | Cisco: non-reserved VLAN 2–4094; MikroTik: 1–4094 |
| `interface_name`, `access_vlan`, `interface_description` | Full L2 port name, an existing VLAN and a single-line ASCII description |
| `allow_trunk_conversion` | Allow changing a trunk port to access; only after checking the port's role (default `false`) |
| `ntp_server`, `syslog_server` | IPv4 destinations in your environment |
| `parent_interface` | Existing RouterOS parent interface; bridge filtering and membership are not changed |
| `dhcp_server`, `lease_address`, `lease_mac`, `lease_comment` | Existing DHCP server, reserved IPv4, MAC and comment |
| `route_name`, `route_destination`, `route_gateway`, `route_distance` | Unique name, canonical IPv4 network, gateway and distance 1–254 |
| `allow_default_route` | Required to add a `/0` route (default `false`) |

On RouterOS, missing resources are created and matching ones are left alone; conflicting resources are never overwritten, and an existing lease's comment is not rewritten on its own. Each apply writes a new snapshot on the controller, so a new local file does not mean the device changed.

## Rollback and limits

- A snapshot is kept before each change. To roll back, compare the operation with the snapshot and revert only that change; don't replace a whole configuration without matching version and topology. Newly created VLANs, routes and leases are not removed automatically.
- Cisco Playbooks manage only the listed fields; they do not change `shutdown`, voice VLAN, STP, port security or ACL attachment.
- A MikroTik VLAN interface does not make end-to-end forwarding work by itself: tagging, the parent, the bridge VLAN table and upstream devices must already be right. DHCP reservations must match the real subnet and pool; a route is only useful if its gateway is reachable.
- NTP/syslog Playbooks manage the configuration only; delivery depends on routing, ACLs and the remote services. `save_config` saves the whole running-config, including earlier unsaved changes.

## Testing status

All Playbooks pass `ansible-playbook --syntax-check` in CI with the pinned collections. Syntax checks do not replace running them against your own Cisco and RouterOS versions in a lab.

## References

[cisco.ios.ios_config](https://docs.ansible.com/projects/ansible/latest/collections/cisco/ios/ios_config_module.html) · [community.routeros.command](https://docs.ansible.com/projects/ansible/latest/collections/community/routeros/command_module.html) · [RouterOS quoting filters](https://docs.ansible.com/projects/ansible/latest/collections/community/routeros/docsite/quoting.html) · MikroTik: [Configuration management](https://help.mikrotik.com/docs/spaces/ROS/pages/328155/Configuration+Management), [VLAN](https://help.mikrotik.com/docs/spaces/ROS/pages/88014957/VLAN), [DHCP](https://help.mikrotik.com/docs/spaces/ROS/pages/24805500/DHCP), [IP routing](https://help.mikrotik.com/docs/spaces/ROS/pages/328084/IP+Routing)
