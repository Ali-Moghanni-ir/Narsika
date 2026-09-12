#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$0")/.."
trap 'code=$?; echo "Installation stopped (exit $code). Existing configuration and data have not been deleted." >&2; exit "$code"' ERR
if [[ ${1:-} == --install-system-packages ]]; then shift; fi
if [[ $# -gt 0 ]]; then echo 'Usage: bash run_linux.sh [--native | --docker]'; exit 2; fi
. /etc/os-release
. tools/native_platform.sh
if ! narsika_platform_supported "${ID:-}" "${VERSION_ID:-}" "$(uname -m)"; then
  echo 'Native installation supports Ubuntu 24.04 or newer on x86_64. For other systems use --docker.' >&2
  exit 1
fi
if [[ ! -d /run/systemd/system ]]; then
  echo 'systemd must be running. On WSL2 enable systemd in /etc/wsl.conf and restart WSL first.' >&2
  exit 1
fi
if [[ ! -t 0 || ! -t 1 ]]; then echo 'Run this installer in an interactive terminal.' >&2; exit 1; fi
if [[ $EUID -ne 0 ]]; then
  command -v sudo >/dev/null || { echo 'Run as root or install sudo.' >&2; exit 1; }
  exec sudo --preserve-env=SSH_CONNECTION bash "$PWD/tools/run_native_linux.sh"
fi
# Do not depend on the invoking user's PATH for root-owned system utilities.
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
required_programs=(ssh ping ufw runuser ss useradd hostname)
missing=0
python3 -c 'import venv, ensurepip, ctypes, sys; assert sys.version_info >= (3, 12); ctypes.CDLL("libssh.so.4")' 2>/dev/null || missing=1
for program in "${required_programs[@]}"; do command -v "$program" >/dev/null || missing=1; done
if [[ $missing -eq 1 ]]; then
  apt-get update
  apt-get install -y python3 python3-venv openssh-client iputils-ping libssh-4 ca-certificates ufw util-linux iproute2 passwd hostname
fi
for program in "${required_programs[@]}"; do
  command -v "$program" >/dev/null || {
    echo "Required system tool '$program' is unavailable after package installation." >&2
    exit 1
  }
done
python3 -c 'import venv, ensurepip, ctypes, sys; assert sys.version_info >= (3, 12); ctypes.CDLL("libssh.so.4")' 2>/dev/null || {
  echo 'Python 3.12 or newer with venv and libssh is required, but Ubuntu packages did not provide a usable runtime.' >&2
  exit 1
}
exec python3 tools/install_native.py
