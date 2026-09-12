#!/usr/bin/env bash
# Shared native-platform gate. Keep this file side-effect free so it can be tested safely.
narsika_platform_supported() {
  local distribution=${1:-}
  local version=${2:-}
  local architecture=${3:-}
  [[ $distribution == ubuntu && $architecture == x86_64 ]] || return 1
  [[ $version =~ ^[0-9]+([.][0-9]+)*$ ]] || return 1
  command -v dpkg >/dev/null || return 1
  dpkg --compare-versions "$version" ge 24.04
}
