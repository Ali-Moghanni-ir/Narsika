"""Platform drivers: what each device type is and how Narsika talks to it.

Every platform-specific decision goes through this registry: the labels shown
in the interface, the operations a platform supports, and the driver that
collects health and interfaces, exports a configuration backup, verifies a
discovered host and builds the Ansible connection variables. Adding a device
type means adding one Platform entry and one driver module, instead of
scattered vendor checks.
"""
from dataclasses import dataclass
from importlib import import_module
from types import ModuleType

from ...security import fail

HEALTH = 'health'
INTERFACES = 'interfaces'
BACKUP = 'backup'
PLAYBOOKS = 'playbooks'
VLAN = 'vlan'
FIREWALL = 'firewall'


@dataclass(frozen=True)
class Platform:
    """Static description of a device type. Behaviour lives in its driver module."""

    id: str
    label: str
    vendor: str
    os_type: str
    driver_module: str
    capabilities: frozenset[str]

    def supports(self, capability: str) -> bool:
        return capability in self.capabilities

    def public(self) -> dict:
        return {'id': self.id, 'label': self.label, 'vendor': self.vendor,
                'capabilities': sorted(self.capabilities)}


PLATFORMS: dict[str, Platform] = {
    'cisco': Platform('cisco', 'Cisco IOS / IOS XE', 'Cisco', 'cisco_ios', 'cisco',
                      frozenset({HEALTH, INTERFACES, BACKUP, PLAYBOOKS, VLAN, FIREWALL})),
    'mikrotik': Platform('mikrotik', 'MikroTik RouterOS', 'MikroTik', 'mikrotik_routeros', 'mikrotik',
                         frozenset({HEALTH, INTERFACES, BACKUP, PLAYBOOKS, FIREWALL})),
    'linux': Platform('linux', 'Linux server', 'Linux', 'linux', 'linux',
                      frozenset({HEALTH, INTERFACES, BACKUP, PLAYBOOKS})),
}

CAPABILITY_NAMES = {HEALTH: 'Health monitoring', INTERFACES: 'Interface monitoring', BACKUP: 'Configuration backup',
                    PLAYBOOKS: 'Playbooks', VLAN: 'VLAN management', FIREWALL: 'Firewall'}


def get(platform_id: object) -> Platform:
    """Return a registered platform or reject the value as a validation error."""
    platform = PLATFORMS.get(platform_id) if isinstance(platform_id, str) else None
    if platform is None:
        fail('Select a supported platform: ' + ', '.join(p.label for p in PLATFORMS.values()) + '.')
    return platform


def of(device: object) -> Platform:
    """The platform of a device or device-like object."""
    return get(getattr(device, 'platform', None))


def require(device: object, capability: str) -> Platform:
    """Fail before any network access when a platform does not offer an operation."""
    platform = of(device)
    if not platform.supports(capability):
        fail(f'{CAPABILITY_NAMES[capability]} is not available for {platform.label}.', 'UNSUPPORTED', 422)
    return platform


def driver(device: object) -> ModuleType:
    """The module that implements a device's platform-specific operations."""
    return import_module(__name__ + '.' + of(device).driver_module)


def vendors() -> tuple[str, ...]:
    """Playbook vendor names, in registry order."""
    return tuple(p.vendor for p in PLATFORMS.values())


def by_vendor(vendor: str) -> Platform | None:
    return next((p for p in PLATFORMS.values() if p.vendor == vendor), None)


def public() -> list[dict]:
    return [p.public() for p in PLATFORMS.values()]
