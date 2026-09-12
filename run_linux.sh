#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$0")"
case "${1:-}" in
  --docker) shift; exec bash tools/run_docker_linux.sh "$@" ;;
  --native|--install-system-packages) shift ;;
  --help|-h) echo 'Usage: bash run_linux.sh [--native | --docker]'; exit 0 ;;
esac
exec bash tools/run_native_linux.sh "$@"
