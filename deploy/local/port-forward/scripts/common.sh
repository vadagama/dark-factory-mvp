#!/usr/bin/env bash
# Shared helpers for the local port-forward launchd tooling (T-093).
# Self-contained copy of the small helpers in deploy/argocd/scripts/common.sh
# (itself a copy of the bootstrap/ci helpers) so this tier does not depend on
# the directory layout of the other deploy tiers.
# shellcheck shell=bash source-path=SCRIPTDIR

set -euo pipefail

# Context baked into the launchd agents at install time (same default as the
# other deploy tiers; override with KUBECTL_CTX=<name> ./install.sh).
: "${KUBECTL_CTX:=docker-desktop}"

info() { printf '[ info ] %s\n' "$*"; }
ok() { printf '[ ok ] %s\n' "$*"; }
warn() { printf '[ warn ] %s\n' "$*"; }
err() { printf '[ error ] %s\n' "$*" >&2; }
die() { err "$*"; exit 1; }

require_cmds() {
  local missing="" cmd
  for cmd in "$@"; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
      missing+="${cmd} "
    fi
  done
  if [[ -n "$missing" ]]; then
    die "missing required commands: ${missing% }"
  fi
}
