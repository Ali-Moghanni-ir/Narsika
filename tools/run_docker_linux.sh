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
    arch|manjaro) "${admin[@]}" pacman -S --needed --noconfirm docker docker-buildx docker-compose ;;
    opensuse*|sles) "${admin[@]}" zypper --non-interactive install docker docker-buildx docker-compose ;;
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
# Run a Docker command, show its output live and keep the last cause line printed by
# Narsika's setup tools, so the final error can repeat it. Pure bash: no tee or awk.
setup_cause=''
run_streamed() {
  local line status=1
  setup_cause=''
  while IFS= read -r line; do
    if [[ $line == '__NARSIKA_EXIT '* ]]; then status=${line#__NARSIKA_EXIT }; continue; fi
    printf '%s\n' "$line"
    if [[ $line == *'NARSIKA_SETUP_CAUSE: '* ]]; then setup_cause=${line#*NARSIKA_SETUP_CAUSE: }; fi
  # An && || list: errexit and the ERR trap must not end the subshell before it reports the status.
  # The leading newline keeps the marker on its own line when the output does not end with one.
  done < <("$@" 2>&1 && printf '\n__NARSIKA_EXIT 0\n' || printf '\n__NARSIKA_EXIT %s\n' "$?")
  return "$status"
}
stop_with_cause() {
  echo "Narsika setup stopped: ${setup_cause:-$1}" >&2
  echo 'Existing data is preserved. Correct the issue, then rerun this launcher.' >&2
  exit 1
}
# Proxy settings reach only the network check and the build. Docker does not store its
# predefined proxy build arguments in the image configuration or history.
proxy=${HTTPS_PROXY:-${https_proxy:-${HTTP_PROXY:-${http_proxy:-}}}}
proxy_env=() proxy_args=() build_network=()
if [[ -n $proxy ]]; then
  no_proxy_value=${NO_PROXY:-${no_proxy:-localhost,127.0.0.1}}
  for name in HTTP_PROXY HTTPS_PROXY http_proxy https_proxy; do
    proxy_env+=(-e "$name=$proxy"); proxy_args+=(--build-arg "$name=$proxy")
  done
  for name in NO_PROXY no_proxy; do
    proxy_env+=(-e "$name=$no_proxy_value"); proxy_args+=(--build-arg "$name=$no_proxy_value")
  done
  address=${proxy#*://}; address=${address##*@}; address=${address%%/*}
  host=${address%:*}
  case $host in
    # 127.0.0.1 inside a container is the container itself; share the host network for these steps only.
    127.*|localhost|'[::1]') build_network=(--network host) ;;
  esac
  label=${proxy%%://*}://$address; [[ $proxy == *://* ]] || label=$address
  echo "Using the proxy $label for the network check and the image build only."
fi
base_image=''
while IFS= read -r line; do
  if [[ $line =~ ^FROM[[:space:]]+([^[:space:]]+) ]]; then base_image=${BASH_REMATCH[1]}; break; fi
done < Dockerfile
[[ -n $base_image ]] || { echo 'The Dockerfile has no FROM line. Extract the release again.' >&2; exit 1; }
check_env=("${proxy_env[@]}")
if [[ -n ${NARSIKA_PIP_INDEX_URL:-} ]]; then check_env+=(-e "NARSIKA_PIP_INDEX_URL=$NARSIKA_PIP_INDEX_URL"); fi
echo 'Checking network access for the build (package index and files, Ansible collections, Debian packages).'
# label=disable: SELinux engines deny unlabelled bind mounts; it is ignored elsewhere and relabels nothing.
if ! run_streamed "${docker_cmd[@]}" run --rm "${build_network[@]}" "${check_env[@]}" -v "$PWD/tools/network_check.py:/narsika-network-check.py:ro" --security-opt label=disable --entrypoint python "$base_image" /narsika-network-check.py; then
  stop_with_cause "Docker could not run the base image $base_image for the network check. If Docker Hub is unreachable, configure the Docker daemon proxy and rerun."
fi
# Build first: configuration generation runs inside the image, without host Python.
build_args=()
for setting in NARSIKA_PIP_INDEX_URL NARSIKA_PIP_TIMEOUT NARSIKA_PIP_RETRIES; do
  if [[ -n ${!setting:-} ]]; then build_args+=(--build-arg "$setting=${!setting}"); fi
done
echo 'Building Narsika with cached dependencies. First installation still downloads the base image, Python packages and Ansible collections.'
echo 'After installation, use docker compose up -d --no-build to start without running setup again.'
# --provenance=false: an attestation would record the build arguments, including a proxy URL.
if ! run_streamed "${docker_cmd[@]}" build --progress=plain "${build_network[@]}" "${build_args[@]}" "${proxy_args[@]}" --provenance=false -t narsika:local .; then
  stop_with_cause 'The image build failed. Read the error above.'
fi
# An existing database can only be decrypted with the keys in the old installation's .env.
data_volume="${COMPOSE_PROJECT_NAME:-narsika}_narsika-data"
if [[ ! -e .env ]] && "${docker_cmd[@]}" volume inspect "${data_volume,,}" >/dev/null 2>&1 \
  && "${docker_cmd[@]}" run --rm --network none -v "${data_volume,,}:/data:ro" --entrypoint test narsika:local -e /data/narsika.db; then
  setup_cause=''
  stop_with_cause 'An existing Narsika data volume was found, but this folder has no .env. Copy .env from your previous Narsika folder into this folder (see docs/OPERATIONS.md), then rerun. New keys would make the existing data unreadable.'
fi
# Rootless Docker and Docker Desktop map container root to the invoking user and every other uid
# to a subordinate uid that cannot write this folder, so .env must be created as container root there.
setup_user="$(id -u):$(id -g)"
if [[ $("${docker_cmd[@]}" info --format '{{.OperatingSystem}} {{.SecurityOptions}}' 2>/dev/null) =~ rootless|Docker\ Desktop ]]; then setup_user=0:0; fi
terminal=(); [[ -t 0 && -t 1 ]] && terminal=(-it)
"${docker_cmd[@]}" run --rm "${terminal[@]}" --network none --user "$setup_user" -v "$PWD:/setup" --security-opt label=disable --entrypoint python narsika:local /setup/configure.py
if ! run_streamed "${docker_cmd[@]}" run --rm --network none --user "$setup_user" -v "$PWD:/setup:ro" --security-opt label=disable --entrypoint python narsika:local /setup/configure.py --check; then
  stop_with_cause 'The configuration check failed. Read the error above.'
fi
"${docker_cmd[@]}" compose stop narsika
"${docker_cmd[@]}" compose run --rm --no-deps bootstrap
"${docker_cmd[@]}" compose up -d --no-build --wait --wait-timeout 120 || { "${docker_cmd[@]}" compose logs --tail=80 narsika; exit 1; }
binding=$("${docker_cmd[@]}" compose port narsika 8000)
echo "Narsika is healthy. Open http://${binding/0.0.0.0/127.0.0.1}"
