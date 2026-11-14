"""Shared pieces for network operating systems reached through Netmiko and Ansible network_cli."""
from pathlib import Path

INVENTORY_GROUPS = ('cisco', 'mikrotik', 'cisco_routers')


def ansible_host_vars(device: object, username: str, values: dict, workdir: Path, network_os: str, become: bool) -> dict:
    """Connection variables for ansible.netcommon.network_cli over libssh with strict host keys."""
    result = dict(ansible_host=device.ip_address, ansible_port=device.ssh_port, ansible_user=username,
                  ansible_password=values.get('password', ''), ansible_become=become, ansible_become_method='enable',
                  ansible_become_password=values.get('enable_password', ''),
                  ansible_connection='ansible.netcommon.network_cli', ansible_network_os=network_os,
                  ansible_network_cli_ssh_type='libssh', ansible_libssh_look_for_keys=False,
                  ansible_host_key_checking=True, ansible_libssh_host_key_checking=True,
                  ansible_libssh_host_key_auto_add=False, ansible_command_timeout=45)
    if values.get('private_key'):
        key = workdir / 'identity'
        key.write_text(values['private_key'])
        key.chmod(0o600)
        result.update(ansible_private_key_file=str(key), ansible_private_key_passphrase=values.get('passphrase', ''))
    return result
