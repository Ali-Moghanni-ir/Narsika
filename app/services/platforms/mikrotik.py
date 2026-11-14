"""MikroTik RouterOS driver: Netmiko for reads and backups, network_cli for Ansible."""
from pathlib import Path

from ...security import fail
from .. import network as net
from ..telemetry import parse_routeros
from . import network_cli

ANSIBLE_GROUPS = network_cli.INVENTORY_GROUPS


def read_health(device: object) -> dict:
    with net.connection(device) as client:
        return parse_routeros(net.command(client, '/system resource print'))


def read_interfaces(device: object) -> list[dict]:
    rows = []
    with net.connection(device) as client:
        # The CLI prints read-only interface state; counters/rates require SNMPv3.
        output = net.command(client, ':foreach i in=[/interface find] do={ :put ([/interface get $i name]."|".'
                                     '[/interface get $i running]."|".[/interface get $i disabled]) }')
    for line in output.splitlines():
        parts = line.strip().split('|')
        if len(parts) == 3 and parts[1] in ('true', 'false'):
            rows.append(dict(index=len(rows)+1, name=parts[0], status='online' if parts[1] == 'true' else 'offline',
                             rx_bps=None, tx_bps=None, speed_mbps=None, rx_errors=None, tx_errors=None))
    if not rows:
        fail('Interface output is unsupported. Configure SNMPv3 for IF-MIB collection.', 'UNSUPPORTED', 502)
    return rows


def export_configuration(device: object) -> str:
    with net.connection(device) as client:
        version = net.command(client, ':put [/system resource get version]')
        if not version.strip().startswith('7.'):
            # RouterOS 6 has a different explicit redaction flag.
            return net.command(client, '/export hide-sensitive')
        output = net.command(client, '/export terse')
    if '#error exporting' in output.lower():
        fail('RouterOS returned a partial export.', 'PARTIAL_EXPORT', 502)
    return output


def verify_identity(target: object) -> bool:
    with net.connection(target, enable=False) as client:
        output = net.command(client, '/system resource print')
    return 'version:' in output and ('board-name:' in output or 'architecture-name:' in output)


def ansible_host_vars(device: object, username: str, values: dict, workdir: Path) -> dict:
    return network_cli.ansible_host_vars(device, username, values, workdir, 'community.routeros.routeros',
                                         become=False)


def ansible_environment(values: dict, control_dir: Path) -> dict:
    return {}
