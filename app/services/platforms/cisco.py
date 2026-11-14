"""Cisco IOS / IOS XE driver: Netmiko for reads and backups, network_cli for Ansible."""
import re
from pathlib import Path

from ...security import APIError, fail
from .. import network as net
from ..telemetry import number, parse_cisco, speed_mbps
from . import network_cli

ANSIBLE_GROUPS = network_cli.INVENTORY_GROUPS


def read_health(device: object) -> dict:
    with net.connection(device) as client:
        version = net.command(client, 'show version')

        def optional(cmd: str) -> str:
            try:
                return net.command(client, cmd)
            except APIError:
                return ''

        return parse_cisco(version, optional('show processes cpu'), optional('show memory statistics'),
                           optional('show environment temperature status'))


def read_interfaces(device: object) -> list[dict]:
    rows = []
    with net.connection(device) as client:
        parsed = client.send_command('show interfaces', use_textfsm=True)
        if not isinstance(parsed, list):
            fail('Interface parser is unavailable for this device output. Configure SNMPv3.', 'UNSUPPORTED', 502)
        for i, r in enumerate(parsed):
            state = r.get('link_status', '').lower()
            up = state == 'up' and r.get('protocol_status', '').lower() == 'up'
            rows.append(dict(index=i+1, name=r.get('interface', ''), status='online' if up else 'offline',
                             speed_mbps=speed_mbps(r.get('speed')), rx_bps=number(r.get('input_rate')),
                             tx_bps=number(r.get('output_rate')), rx_errors=number(r.get('input_errors')),
                             tx_errors=number(r.get('output_errors'))))
    return rows


def export_configuration(device: object) -> str:
    with net.connection(device) as client:
        return net.command(client, 'show running-config')


def verify_identity(target: object) -> bool:
    with net.connection(target, enable=False) as client:
        output = net.command(client, 'show version')
    return bool(re.search(r'Cisco.*(?:IOS|Software)|Cisco IOS|Catalyst', output, re.I))


def ansible_host_vars(device: object, username: str, values: dict, workdir: Path) -> dict:
    return network_cli.ansible_host_vars(device, username, values, workdir, 'cisco.ios.ios',
                                         become=bool(values.get('enable_password')))


def ansible_environment(values: dict, control_dir: Path) -> dict:
    return {}
