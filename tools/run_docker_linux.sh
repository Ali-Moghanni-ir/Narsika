#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$0")/.."
trap 'code=$?; echo "Narsika setup failed (exit $code). Read the error above, then rerun this launcher; existing data is preserved." >&2; exit "$code"' ERR
if [[ $# -gt 0 ]]; then echo 'Usage: bash run_linux.sh --docker'; exit 2; fi
admin=()
if [[ $EUID -ne 0 ]]; then
  admin=(sudo)
fi
require_admin() {
  if [[ $EUID -ne 0 ]]; then
    command -v sudo >/dev/null || { echo 'Missing system prerequisites require root. Install sudo or run this installer as root.' >&2; exit 1; }
    sudo -v
  fi
}
install_docker() {
  require_admin
  . /etc/os-release
  case "$ID" in
    ubuntu|debian)
      "${admin[@]}" apt-get update
      "${admin[@]}" apt-get install -y ca-certificates curl
      "${admin[@]}" install -m 0755 -d /etc/apt/keyrings
      "${admin[@]}" curl -fsSL "https://download.docker.com/linux/$ID/gpg" -o /etc/apt/keyrings/docker.asc
      "${admin[@]}" chmod a+r /etc/apt/keyrings/docker.asc
      printf 'Types: deb\nURIs: https://download.docker.com/linux/%s\nSuites: %s\nComponents: stable\nArchitectures: %s\nSigned-By: /etc/apt/keyrings/docker.asc\n' "$ID" "${UBUNTU_CODENAME:-$VERSION_CODENAME}" "$(dpkg --print-architecture)" | "${admin[@]}" tee /etc/apt/sources.list.d/narsika-docker.sources >/dev/null
      "${admin[@]}" apt-get update
      "${admin[@]}" apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
      ;;
    fedora|rhel|centos|rocky|almalinux)
      "${admin[@]}" dnf install -y ca-certificates curl
      repo_os=centos
      case "$ID" in fedora) repo_os=fedora ;; rhel) repo_os=rhel ;; esac
      "${admin[@]}" curl -fsSL "https://download.docker.com/linux/$repo_os/docker-ce.repo" -o /etc/yum.repos.d/docker-ce.repo
      "${admin[@]}" dnf install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
      ;;
    arch|manjaro) "${admin[@]}" pacman -S --needed --noconfirm docker docker-compose ;;
    opensuse*|sles) "${admin[@]}" zypper --non-interactive install docker docker-compose ;;
    *) echo "Automatic Docker installation is not supported on $ID. Install Docker Engine and Compose v2, then rerun." >&2; exit 1 ;;
  esac
}
if ! command -v docker >/dev/null; then install_docker; fi
docker_cmd=(docker)
if ! docker info >/dev/null 2>&1; then
  require_admin
  if command -v systemctl >/dev/null; then "${admin[@]}" systemctl enable --now docker
  elif command -v service >/dev/null; then "${admin[@]}" service docker start
  else echo 'No service manager found. Start the Docker daemon, then rerun.' >&2; exit 1; fi
  if ! docker info >/dev/null 2>&1; then
    docker_cmd=("${admin[@]}" docker)
  fi
fi
"${docker_cmd[@]}" info >/dev/null
[[ $("${docker_cmd[@]}" info --format '{{.OSType}}') == linux ]] || { echo 'Select a Linux Docker engine.' >&2; exit 1; }
if ! "${docker_cmd[@]}" compose up --help 2>/dev/null | grep -q -- --wait-timeout; then
  require_admin
  command -v curl >/dev/null || {
    . /etc/os-release
    case "$ID" in
      ubuntu|debian) "${admin[@]}" apt-get update; "${admin[@]}" apt-get install -y curl ca-certificates ;;
      fedora|rhel|centos|rocky|almalinux) "${admin[@]}" dnf install -y curl ca-certificates ;;
      arch|manjaro) "${admin[@]}" pacman -S --needed --noconfirm curl ca-certificates ;;
      opensuse*|sles) "${admin[@]}" zypper --non-interactive install curl ca-certificates ;;
      *) echo 'Install curl and CA certificates, then rerun.' >&2; exit 1 ;;
    esac
  }
  # Preserve an existing engine. Install the official CLI plugin with checksum verification.
  architecture=$(uname -m)
  case "$architecture" in x86_64|aarch64) ;; *) echo "Unsupported automatic Compose download: $architecture" >&2; exit 1 ;; esac
  temporary=$(mktemp -d)
  artifact="docker-compose-linux-$architecture"
  release='https://github.com/docker/compose/releases/download/v5.5.0'
  curl --retry 3 -fL "$release/$artifact" -o "$temporary/$artifact"
  curl --retry 3 -fL "$release/$artifact.sha256" -o "$temporary/checksum"
  expected=$(awk '{print $1}' "$temporary/checksum")
  [[ "$expected" =~ ^[a-fA-F0-9]{64}$ ]] || { echo 'Invalid Compose checksum.' >&2; exit 1; }
  printf '%s  %s\n' "$expected" "$temporary/$artifact" | sha256sum -c -
  "${admin[@]}" install -d /usr/local/lib/docker/cli-plugins
  "${admin[@]}" install -m 0755 "$temporary/$artifact" /usr/local/lib/docker/cli-plugins/docker-compose
  rm -r "$temporary"
fi
"${docker_cmd[@]}" compose version
"${docker_cmd[@]}" compose up --help | grep -q -- --wait-timeout || { echo 'An older user-level Compose plugin is shadowing the installed plugin. Update that plugin and rerun.' >&2; exit 1; }
# Build first: configuration generation runs inside the image, without host Python.
"${docker_cmd[@]}" build -t narsika:local .
terminal=(); [[ -t 0 && -t 1 ]] && terminal=(-it)
"${docker_cmd[@]}" run --rm "${terminal[@]}" --network none --user "$(id -u):$(id -g)" -v "$PWD:/setup" --entrypoint python narsika:local /setup/configure.py
"${docker_cmd[@]}" run --rm --network none --user "$(id -u):$(id -g)" -v "$PWD:/setup:ro" --entrypoint python narsika:local /setup/configure.py --check
"${docker_cmd[@]}" compose stop narsika
"${docker_cmd[@]}" compose run --rm --no-deps bootstrap
"${docker_cmd[@]}" compose up -d --no-build --wait --wait-timeout 120 || { "${docker_cmd[@]}" compose logs --tail=80 narsika; exit 1; }
binding=$("${docker_cmd[@]}" compose port narsika 8000)
echo "Narsika is healthy. Open http://${binding/0.0.0.0/127.0.0.1}"
