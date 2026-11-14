"""Linux server driver.

Health, interfaces, configuration snapshots and identity checks each run one
fixed POSIX shell script over an SSH exec channel (``/bin/sh -s`` with the
script on standard input) as the credential's own user. The scripts only read:
they never use sudo and never write on the server. Playbooks use Ansible's
``ssh`` connection.

Both paths trust only the host keys in Narsika's known_hosts file. An unknown
key is refused and a changed key is reported, as for network devices.
"""
import math
import re
import shlex
import socket
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import paramiko
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization

from ...security import address, fail
from .. import network as net

ANSIBLE_GROUPS = ('linux',)
INTERFACE_RATE_NOTE = 'SSH rates are averaged over one second of kernel interface counters.'

SECTION = re.compile(r'^<<<narsika:([a-z0-9-]+)(?: ([^<>]*))?>>>$')
DIGITS = re.compile(r'[0-9]+')
DECIMAL = re.compile(r'-?[0-9]+(?:\.[0-9]+)?')
SNAPSHOT_SECTION = '##### narsika: '
SNAPSHOT_END = SNAPSHOT_SECTION + 'end of snapshot'
HEALTH_LIMIT = 1024 * 1024
INTERFACE_LIMIT = 2 * 1024 * 1024
SNAPSHOT_LIMIT = 16 * 1024 * 1024
MAX_INTERFACES = 512
MAX_FILESYSTEMS = 32
MAX_FAILED_UNITS = 20

# Shared prelude. Everything runs inside one { ... } group with standard input
# from /dev/null: the shell reads the whole script first, so no command can
# consume the rest of it. Commands that may hang (df on a stale mount,
# systemctl without D-Bus) are bounded when timeout(1) exists.
PRELUDE = r'''exec 2>/dev/null
LC_ALL=C
LANG=C
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$PATH
export LC_ALL LANG PATH
mark() { printf '<<<narsika:%s>>>\n' "$1"; }
limited() { if command -v timeout >/dev/null; then timeout 10 "$@"; else "$@"; fi; }
'''

VERIFY_SCRIPT = '{\n' + PRELUDE + r'''mark kernel
uname -s
mark end
} </dev/null
'''

HEALTH_SCRIPT = '{\n' + PRELUDE + r'''mark kernel
uname -s
uname -r
uname -m
mark hostname
uname -n
mark os-release
cat /etc/os-release || cat /usr/lib/os-release
mark cpu
read -r line < /proc/stat && printf '%s\n' "$line"
sleep 1
read -r line < /proc/stat && printf '%s\n' "$line"
mark memory
cat /proc/meminfo
mark uptime
cat /proc/uptime
mark load
cat /proc/loadavg
mark cpus
getconf _NPROCESSORS_ONLN || nproc
mark filesystems
limited df -P -k -l || limited df -P -k
mark temperature
for file in /sys/class/thermal/thermal_zone*/temp /sys/class/hwmon/hwmon*/temp*_input; do
  value=
  [ -r "$file" ] && read -r value < "$file" && printf '%s\n' "$value"
done
mark model
for file in /sys/class/dmi/id/sys_vendor /sys/class/dmi/id/product_name; do
  value=
  [ -r "$file" ] && read -r value < "$file"
  printf '%s\n' "$value"
done
mark board
[ -r /proc/device-tree/model ] && tr -d '\000' < /proc/device-tree/model && printf '\n'
mark failed-units
if [ -d /run/systemd/system ] && command -v systemctl >/dev/null; then
  limited systemctl list-units --state=failed --no-legend --plain --no-pager && mark systemd
fi
mark end
} </dev/null
'''

INTERFACES_SCRIPT = '{\n' + PRELUDE + r'''mark kernel
uname -s
sample() {
  read -r up rest < /proc/uptime
  mark "sample $up"
  for dev in /sys/class/net/*; do
    [ -d "$dev" ] || continue
    name=${dev##*/}
    [ "$name" = lo ] && continue
    index=
    state=
    carrier=
    speed=
    rx=
    tx=
    rxe=
    txe=
    read -r index < "$dev/ifindex"
    read -r state < "$dev/operstate"
    read -r carrier < "$dev/carrier"
    read -r speed < "$dev/speed"
    read -r rx < "$dev/statistics/rx_bytes"
    read -r tx < "$dev/statistics/tx_bytes"
    read -r rxe < "$dev/statistics/rx_errors"
    read -r txe < "$dev/statistics/tx_errors"
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$name" "$index" "$state" "$carrier" "$speed" "$rx" "$tx" "$rxe" "$txe"
  done
}
sample
sleep 1
sample
mark end
} </dev/null
'''

# Readable configuration files worth keeping. Files that normally hold private
# keys or passphrases (shadow, SSH host keys, WireGuard, NetworkManager
# connections, SNMP communities) are deliberately not listed.
SNAPSHOT_FILES = '''/etc/hostname
/etc/hosts
/etc/resolv.conf
/etc/nsswitch.conf
/etc/fstab
/etc/network/interfaces
/etc/network/interfaces.d/*
/etc/netplan/*.yaml
/etc/netplan/*.yml
/etc/sysconfig/network
/etc/sysconfig/network-scripts/ifcfg-*
/etc/NetworkManager/NetworkManager.conf
/etc/systemd/network/*.network
/etc/systemd/network/*.netdev
/etc/systemd/network/*.link
/etc/systemd/resolved.conf
/etc/systemd/timesyncd.conf
/etc/sysctl.conf
/etc/sysctl.d/*.conf
/etc/ssh/sshd_config
/etc/ssh/sshd_config.d/*.conf
/etc/chrony.conf
/etc/chrony/chrony.conf
/etc/ntp.conf
/etc/rsyslog.conf
/etc/rsyslog.d/*.conf
/etc/nftables.conf
/etc/iptables/rules.v4
/etc/iptables/rules.v6
/etc/sysconfig/iptables
/etc/sysconfig/ip6tables
/etc/ufw/ufw.conf
/etc/ufw/user.rules
/etc/ufw/user6.rules
/etc/firewalld/firewalld.conf
/etc/firewalld/zones/*.xml
/etc/crontab
/etc/cron.d/*
/etc/sudoers
/etc/sudoers.d/*
/etc/passwd
/etc/group
/etc/apt/sources.list
/etc/apt/sources.list.d/*.list
/etc/apt/sources.list.d/*.sources
/etc/yum.repos.d/*.repo'''

SNAPSHOT_SCRIPT = '{\n' + PRELUDE + r'''section() { printf '##### narsika: %s\n' "$*"; }
section 'Linux configuration snapshot (read-only, collected without sudo)'
section system
uname -s -r -m
uname -n
section os-release
cat /etc/os-release || cat /usr/lib/os-release
section links
limited ip -o link show | awk '{
  name = $2; sub(/:$/, "", name); admin = ($3 ~ /[<,]UP[,>]/) ? "up" : "down"; mtu = ""; link = ""
  for (i = 4; i <= NF; i++) {
    if ($i == "mtu") mtu = $(i + 1)
    if ($i ~ /^link\//) { link = $i; if ($(i + 1) ~ /:/) link = link " " $(i + 1) }
  }
  print name, "admin=" admin, "mtu=" mtu, link
}'
section addresses
limited ip -o address show | awk '{ print $2, $3, $4 }'
section routes
limited ip -4 route show table all
limited ip -6 route show table all | sed 's/ expires [0-9]*sec//'
section policy-rules
limited ip -4 rule show
limited ip -6 rule show
section listening-sockets
limited ss -l -n -t -u | awk '$1 != "Netid" { print $1, $5 }' | sort -u
section enabled-units
if command -v systemctl >/dev/null; then
  limited systemctl list-unit-files --state=enabled --no-legend --no-pager | awk '{ print $1 }' | sort
fi
section packages
if command -v dpkg-query >/dev/null; then
  limited dpkg-query -W -f '${Status}\t${binary:Package}\t${Version}\n' | awk -F '\t' '$1 ~ / installed$/ { print $2, $3 }' | sort
elif command -v rpm >/dev/null; then
  limited rpm -qa --qf '%{NAME} %{VERSION}-%{RELEASE}.%{ARCH}\n' | sort
elif command -v apk >/dev/null; then
  limited apk info -v | sort
elif command -v pacman >/dev/null; then
  limited pacman -Q | sort
fi
files='__FILES__'
for path in $files; do
  [ -f "$path" ] || continue
  if [ ! -r "$path" ]; then
    section "file $path (not readable by this account)"
    continue
  fi
  size=$(wc -c < "$path" | tr -d ' ')
  case $size in
    ''|*[!0-9]*) section "file $path (size unavailable)"; continue ;;
  esac
  if [ "$size" -gt 262144 ]; then
    section "file $path (skipped: $size bytes)"
    continue
  fi
  section "file $path"
  cat "$path"
  [ -n "$(tail -c 1 "$path")" ] && printf '\n'
done
section 'end of snapshot'
} </dev/null
'''.replace('__FILES__', SNAPSHOT_FILES)

PLACEHOLDERS = frozenset({
    '', 'default string', 'manufacturer', 'n/a', 'none', 'not applicable', 'not specified', 'o.e.m.', 'oem',
    'product name', 'system manufacturer', 'system product name', 'to be filled by o.e.m.', 'type1productconfigid',
    'unknown',
})
PSEUDO_FILESYSTEMS = frozenset({
    'autofs', 'binfmt_misc', 'bpf', 'cgroup', 'cgroup2', 'configfs', 'debugfs', 'devpts', 'devtmpfs', 'efivarfs',
    'fusectl', 'hugetlbfs', 'mqueue', 'none', 'nsfs', 'overlay', 'proc', 'pstore', 'ramfs', 'rpc_pipefs',
    'securityfs', 'shm', 'squashfs', 'sysfs', 'tmpfs', 'tracefs', 'udev',
})
PSEUDO_MOUNT_ROOTS = ('/proc', '/sys', '/dev', '/run', '/snap', '/var/lib/docker', '/var/lib/containers',
                      '/var/lib/kubelet')


@contextmanager
def session(device: object) -> Iterator[paramiko.SSHClient]:
    """An authenticated SSH client that accepts only a host key Narsika already trusts."""
    address(device.ip_address)
    credential = device.credential
    values = net.secret(credential)
    known = str(net.known_path())
    if net.key_host(device.ip_address, device.ssh_port) not in paramiko.HostKeys(known):
        fail('Verify and trust the SSH host key from the device actions menu.', 'HOST_KEY_REQUIRED', 409)
    options = dict(hostname=device.ip_address, port=device.ssh_port, username=credential.username,
                   look_for_keys=False, allow_agent=False, timeout=6, auth_timeout=10, banner_timeout=10)
    if values.get('private_key'):
        options['pkey'] = net.private_key(values)
    if values.get('password'):
        options['password'] = values['password']
    client = paramiko.SSHClient()
    # Loaded as "system" keys: paramiko never writes those back to the file.
    client.load_system_host_keys(known)
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    try:
        client.connect(**options)
        yield client
    except paramiko.BadHostKeyException:
        fail('SSH host key does not match the trusted key.', 'HOST_KEY_CHANGED', 409)
    except paramiko.AuthenticationException:
        fail('Device authentication failed.', 'AUTH_FAILED', 502)
    except paramiko.ssh_exception.NoValidConnectionsError:
        fail('The server refused the SSH connection.', 'UNREACHABLE', 502)
    except (socket.timeout, TimeoutError):
        fail('Device connection timed out.', 'TIMEOUT', 504)
    except (paramiko.SSHException, OSError, EOFError):
        fail('The device did not return a usable SSH response.', 'DEVICE_ERROR', 502)
    finally:
        client.close()


def run_script(client: paramiko.SSHClient, script: str, timeout: float, limit: int) -> str:
    """Run a fixed script with /bin/sh on the server and return its output."""
    deadline = time.monotonic() + timeout
    channel = client.get_transport().open_session(timeout=10)
    try:
        # One stream: unread standard error could otherwise stall the channel window.
        channel.set_combine_stderr(True)
        channel.settimeout(timeout)
        channel.exec_command('/bin/sh -s')
        channel.sendall(script.encode())
        channel.shutdown_write()
        chunks = []
        size = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                fail('The server did not finish the collection in time.', 'TIMEOUT', 504)
            channel.settimeout(remaining)
            chunk = channel.recv(65536)
            if not chunk:
                break
            size += len(chunk)
            if size > limit:
                fail('The server returned more output than Narsika accepts.', 'DEVICE_ERROR', 502)
            chunks.append(chunk)
        channel.status_event.wait(max(0.1, min(5.0, deadline - time.monotonic())))
        status = channel.exit_status if channel.exit_status_ready() else None
    finally:
        channel.close()
    if status not in (0, None):
        fail(f'The server did not run the collection script (exit status {status}). '
             'The account needs a login shell that can run /bin/sh.', 'DEVICE_ERROR', 502)
    return b''.join(chunks).decode('utf-8', errors='replace')


def split_sections(output: str) -> list[tuple[str, str, list[str]]]:
    """Split collector output at its markers. Text before the first marker (shell start-up noise) is ignored."""
    result = []
    for line in output.splitlines():
        match = SECTION.match(line.rstrip('\r'))
        if match:
            result.append((match[1], match[2] or '', []))
        elif result:
            result[-1][2].append(line.rstrip('\r'))
    return result


def sections(output: str) -> dict[str, list[str]]:
    """First occurrence of every section, after checking that the output is complete."""
    result = {}
    for name, _, lines in split_sections(output):
        result.setdefault(name, lines)
    if 'end' not in result:
        fail('The server returned an incomplete response.', 'DEVICE_ERROR', 502)
    if first(result.get('kernel')) != 'Linux':
        fail('The server did not identify itself as Linux.', 'UNSUPPORTED', 502)
    return result


def first(lines: list[str] | None, position: int = 0) -> str | None:
    """A stripped line of a section, or None when the line is missing or empty."""
    if not lines or len(lines) <= position:
        return None
    return lines[position].strip() or None


def digits(text: str) -> bool:
    """True for a plain ASCII number; str.isdigit() also accepts characters int() rejects."""
    return bool(DIGITS.fullmatch(text))


def integer(value: object) -> int | None:
    text = str(value).strip()
    return int(text) if digits(text) else None


def decimal(value: object) -> float | None:
    text = str(value).strip()
    if not DECIMAL.fullmatch(text):
        return None
    result = float(text)
    return result if math.isfinite(result) else None


def cpu_times(line: str | None) -> list[int] | None:
    """user, nice, system, idle, iowait, irq, softirq and steal from a /proc/stat 'cpu' line."""
    fields = (line or '').split()
    if len(fields) < 5 or fields[0] != 'cpu' or not all(digits(value) for value in fields[1:9]):
        return None
    values = [int(value) for value in fields[1:9]]
    # Older kernels report fewer columns; the missing kinds of time did not exist there.
    return values + [0] * (8 - len(values))


def cpu_percent(before: str | None, after: str | None) -> float | None:
    """Busy share of CPU time between two samples. Guest time is already part of user time."""
    start, end = cpu_times(before), cpu_times(after)
    if start is None or end is None:
        return None
    total = sum(end) - sum(start)
    idle = end[3] + end[4] - start[3] - start[4]
    if total <= 0 or not 0 <= idle <= total:
        return None
    return round((total - idle) / total * 100, 1)


def meminfo(lines: list[str]) -> dict[str, int]:
    """/proc/meminfo values in bytes."""
    result = {}
    for line in lines:
        match = re.fullmatch(r'([A-Za-z0-9_()]+):\s+(\d+)(\s+kB)?', line.strip())
        if match:
            result[match[1]] = int(match[2]) * (1024 if match[3] else 1)
    return result


def uptime_text(seconds: int) -> str:
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    parts = [f'{value} {unit}{"" if value == 1 else "s"}'
             for value, unit in ((days, 'day'), (hours, 'hour'), (minutes, 'minute')) if value]
    return ', '.join(parts) if parts else f'{seconds} second{"" if seconds == 1 else "s"}'


def os_release(lines: list[str]) -> dict[str, str]:
    result = {}
    for line in lines:
        key, separator, value = line.strip().partition('=')
        if not separator or not re.fullmatch(r'[A-Z0-9_]+', key):
            continue
        try:
            words = shlex.split(value)
        except ValueError:
            words = [value.strip('"\'')]
        result[key] = ' '.join(words).strip()
    return result


def filesystems(lines: list[str]) -> list[dict]:
    """Local filesystems from POSIX df output. The root filesystem is always kept."""
    result = []
    seen = set()
    for line in lines:
        parts = line.split(None, 5)
        if len(parts) != 6:
            continue
        source, blocks, used, available, capacity, mount = parts
        if not all(digits(value) for value in (blocks, used, available, capacity[:-1])) \
                or not capacity.endswith('%') or mount in seen:
            continue
        pseudo = source in PSEUDO_FILESYSTEMS or int(blocks) == 0 or any(
            mount == root or mount.startswith(root + '/') for root in PSEUDO_MOUNT_ROOTS)
        if pseudo and mount != '/':
            continue
        seen.add(mount)
        result.append(dict(mount=mount, filesystem=source, size_bytes=int(blocks) * 1024,
                           used_bytes=int(used) * 1024, available_bytes=int(available) * 1024,
                           used_percent=int(capacity[:-1])))
    return result[:MAX_FILESYSTEMS]


def temperature(lines: list[str]) -> float | None:
    """Highest plausible sensor reading in degrees Celsius; sysfs reports millidegrees."""
    readings = [int(line.strip()) for line in lines if digits(line.strip())]
    plausible = [value for value in readings if 1000 <= value <= 150000]
    return round(max(plausible) / 1000, 1) if plausible else None


def hardware_model(dmi: list[str], board: list[str]) -> str | None:
    """Vendor and product from DMI, or the device-tree model on boards without DMI."""
    vendor, product = (first(dmi, index) for index in (0, 1))
    vendor = None if vendor is None or vendor.lower() in PLACEHOLDERS else vendor
    product = None if product is None or product.lower() in PLACEHOLDERS else product
    if product and vendor and not product.lower().startswith(vendor.lower()):
        model = vendor + ' ' + product
    else:
        model = product or vendor or first(board)
    return model[:100] if model else None


def load_average(line: str | None) -> list[float] | None:
    values = [decimal(value) for value in (line or '').split()[:3]]
    return values if len(values) == 3 and None not in values else None


def parse_health(output: str) -> dict:
    """Health values from the health script. Anything the server did not report stays None."""
    parts = sections(output)
    cpu = parts.get('cpu', [])
    memory = meminfo(parts.get('memory', []))
    total = memory.get('MemTotal')
    available = memory.get('MemAvailable')
    used = total - available if total and available is not None and available <= total else None
    seconds = decimal((first(parts.get('uptime')) or '').partition(' ')[0])
    seconds = int(seconds) if seconds is not None and seconds >= 0 else None
    release = os_release(parts.get('os-release', []))
    version = release.get('PRETTY_NAME') or ' '.join(filter(None, (release.get('NAME'), release.get('VERSION_ID'))))
    disks = filesystems(parts.get('filesystems', []))
    fullest = max(disks, key=lambda disk: disk['used_percent'], default=None)
    # The marker after systemctl tells "no failed units" apart from "could not ask systemd".
    units = [line.split()[0] for line in parts.get('failed-units', []) if line.strip()] if 'systemd' in parts else None
    kernel = parts.get('kernel', [])
    return dict(
        cpu_percent=cpu_percent(first(cpu, 0), first(cpu, 1)),
        memory_percent=round(used / total * 100, 1) if used is not None else None,
        memory_used_bytes=used,
        # Linux: memory available to applications (MemAvailable), not the smaller MemFree.
        memory_free_bytes=available if used is not None else None,
        memory_total_bytes=total,
        uptime=uptime_text(seconds) if seconds is not None else None,
        uptime_seconds=seconds,
        temperature_celsius=temperature(parts.get('temperature', [])),
        model=hardware_model(parts.get('model', []), parts.get('board', [])),
        os_version=version or None,
        kernel=first(kernel, 1),
        architecture=first(kernel, 2),
        hostname=first(parts.get('hostname')),
        load_average=load_average(first(parts.get('load'))),
        cpu_count=integer(first(parts.get('cpus'))),
        disk_percent=fullest['used_percent'] if fullest else None,
        disk_mount=fullest['mount'] if fullest else None,
        filesystems=disks,
        failed_units=len(units) if units is not None else None,
        failed_unit_names=units[:MAX_FAILED_UNITS] if units is not None else None,
    )


def link_status(operstate: str, carrier: str) -> str:
    """Map the kernel operstate to Narsika's link status."""
    if operstate == 'up':
        return 'online'
    if operstate in ('down', 'lowerlayerdown', 'notpresent'):
        return 'offline'
    # Tunnels and some virtual links always report "unknown"; carrier still tells.
    if operstate == 'unknown' and carrier in ('0', '1'):
        return 'online' if carrier == '1' else 'offline'
    return 'unknown'


def interface_rows(lines: list[str]) -> dict[str, dict]:
    rows = {}
    for line in lines:
        fields = line.split('\t')
        if len(fields) != 9 or not fields[0]:
            continue
        name, index, state, carrier, speed, rx, tx, rx_errors, tx_errors = fields
        mbps = integer(speed)
        rows[name] = dict(name=name, ifindex=integer(index), status=link_status(state.strip(), carrier.strip()),
                          speed_mbps=float(mbps) if mbps else None, rx_bytes=integer(rx), tx_bytes=integer(tx),
                          rx_errors=integer(rx_errors), tx_errors=integer(tx_errors))
    return rows


def rate(after: int | None, before: int | None, elapsed: float | None) -> float | None:
    """Bits per second between two counter readings; None after a reset or without a valid interval."""
    if after is None or before is None or not elapsed or elapsed <= 0 or after < before:
        return None
    return round((after - before) * 8 / elapsed, 1)


def parse_interfaces(output: str) -> list[dict]:
    """Interface rows with rates from the two counter samples of the interface script."""
    parts = split_sections(output)
    kernel = next((lines for name, _, lines in parts if name == 'kernel'), None)
    samples = [(decimal(argument), lines) for name, argument, lines in parts if name == 'sample']
    if len(samples) != 2 or not any(name == 'end' for name, _, _ in parts):
        fail('The server returned an incomplete interface sample.', 'DEVICE_ERROR', 502)
    if first(kernel) != 'Linux':
        fail('The server did not identify itself as Linux.', 'UNSUPPORTED', 502)
    (start, before), (end, after) = samples
    elapsed = end - start if start is not None and end is not None else None
    previous = interface_rows(before)
    rows = []
    for row in interface_rows(after).values():
        old = previous.get(row['name'], {})
        row.update(rx_bps=rate(row['rx_bytes'], old.get('rx_bytes'), elapsed),
                   tx_bps=rate(row['tx_bytes'], old.get('tx_bytes'), elapsed))
        rows.append(row)
    rows.sort(key=lambda row: (row['ifindex'] is None, row['ifindex'] or 0, row['name']))
    rows = rows[:MAX_INTERFACES]
    for position, row in enumerate(rows, start=1):
        row['index'] = row.pop('ifindex') or position
    return rows


def clean_snapshot(output: str) -> str:
    """The snapshot from its first section marker to its end marker, without binary file content."""
    lines = output.splitlines()
    start = next((number for number, line in enumerate(lines) if line.startswith(SNAPSHOT_SECTION)), None)
    if start is None or SNAPSHOT_END not in lines[start:]:
        fail('The server returned a partial configuration snapshot.', 'PARTIAL_EXPORT', 502)
    result = []
    body = []
    for line in lines[start:lines.index(SNAPSHOT_END, start) + 1]:
        if not line.startswith(SNAPSHOT_SECTION):
            body.append(line)
            continue
        binary = any('\x00' in text for text in body)
        result.extend(['(binary content not included)'] if binary else body)
        result.append(line)
        body = []
    return '\n'.join(result) + '\n'


def read_health(device: object) -> dict:
    with session(device) as client:
        output = run_script(client, HEALTH_SCRIPT, 40, HEALTH_LIMIT)
    return parse_health(output)


def read_interfaces(device: object) -> list[dict]:
    with session(device) as client:
        output = run_script(client, INTERFACES_SCRIPT, 30, INTERFACE_LIMIT)
    rows = parse_interfaces(output)
    if not rows:
        fail('The server reported no network interfaces besides loopback.', 'UNSUPPORTED', 502)
    return rows


def export_configuration(device: object) -> str:
    with session(device) as client:
        output = run_script(client, SNAPSHOT_SCRIPT, 80, SNAPSHOT_LIMIT)
    return clean_snapshot(output)


def verify_identity(target: object) -> bool:
    with session(target) as client:
        output = run_script(client, VERIFY_SCRIPT, 20, 64 * 1024)
    found = {name: lines for name, _, lines in split_sections(output)}
    return 'end' in found and first(found.get('kernel')) == 'Linux'


def openssh_private_key(values: dict) -> str:
    """The stored key, decrypted and in OpenSSH format, for the ssh-agent Ansible starts for one run."""
    data = values['private_key'].encode()
    passphrase = values.get('passphrase') or ''
    passwords = ([passphrase.encode()] if passphrase else []) + [None]
    for loader in (serialization.load_ssh_private_key, serialization.load_pem_private_key):
        for password in passwords:
            try:
                key = loader(data, password=password)
                return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH,
                                         serialization.NoEncryption()).decode()
            except (TypeError, ValueError, UnsupportedAlgorithm):
                continue
    fail('Private key or passphrase is invalid.', 'AUTH_FAILED', 422)


def ssh_option_path(path: Path) -> str:
    """A file path quoted for an OpenSSH option value."""
    text = str(path)
    # OpenSSH expands %, ${} and ~ in some file options; refuse anything it could reinterpret.
    if not re.fullmatch(r'/[\w./@+=:, -]*', text):
        fail('The data directory path contains characters OpenSSH options cannot carry safely.',
             'CONFIGURATION_ERROR', 500)
    return '"' + text + '"'


def ansible_host_vars(device: object, username: str, values: dict, workdir: Path) -> dict:
    """Variables for Ansible's ssh connection with strict checking against Narsika's known_hosts only."""
    options = ['-F', '/dev/null', '-o', 'StrictHostKeyChecking=yes',
               '-o', 'UserKnownHostsFile=' + ssh_option_path(net.known_path()),
               '-o', 'GlobalKnownHostsFile=/dev/null', '-o', 'UpdateHostKeys=no', '-o', 'IdentitiesOnly=yes']
    result = dict(ansible_host=device.ip_address, ansible_port=device.ssh_port, ansible_user=username,
                  ansible_connection='ssh', ansible_host_key_checking=True,
                  ansible_python_interpreter='auto_silent')
    if values.get('password'):
        result['ansible_password'] = values['password']
    if values.get('private_key'):
        result['ansible_ssh_private_key'] = openssh_private_key(values)
    else:
        # Never offer the Narsika service account's own keys to a server.
        options += ['-o', 'PubkeyAuthentication=no']
    # sudo asks for the user's own password unless the profile stores a separate one.
    become_password = values.get('enable_password') or values.get('password')
    if become_password:
        result['ansible_become_password'] = become_password
    result['ansible_ssh_common_args'] = shlex.join(options)
    return result


def ansible_environment(values: dict, control_dir: Path) -> dict:
    """Run-scoped Ansible settings for Linux targets."""
    environment = {
        'ANSIBLE_PIPELINING': 'True',
        # No ControlPersist master may outlive the job; every task opens its own connection.
        'ANSIBLE_SSH_ARGS': '-o ControlMaster=no',
        'ANSIBLE_SSH_RETRIES': '0',
        # The per-user default; the shared /tmp path used for network devices does not fit multi-user servers.
        'ANSIBLE_REMOTE_TEMP': '~/.ansible/tmp',
        # A short local path: Ansible's ssh-agent socket must fit the Unix socket length limit.
        'ANSIBLE_LOCAL_TEMP': str(control_dir / 'local'),
    }
    if values.get('private_key'):
        environment['ANSIBLE_SSH_AGENT'] = 'auto'
    return environment
