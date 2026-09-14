#!/usr/bin/env bash
# Shared helpers for deploy/argocd scripts (T032). Self-contained copy of the
# small helpers in deploy/ci/scripts/common.sh (which is itself a copy of the
# bootstrap helpers) so the Argo CD tooling does not depend on the directory
# layout of the other deploy tiers.
# shellcheck shell=bash source-path=SCRIPTDIR

set -euo pipefail

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

kctl() { kubectl --context "$KUBECTL_CTX" "$@"; }

cluster_reachable() { kctl cluster-info --request-timeout=10s >/dev/null 2>&1; }

# Prints an actionable message when the cluster API is unreachable, most
# commonly because Kubernetes is disabled in Docker Desktop (same pattern as
# deploy/bootstrap and deploy/ci).
k8s_disabled_hint() {
  err "Kubernetes API of cluster '$KUBECTL_CTX' is not reachable (kubectl cluster-info failed)."
  err "To enable Kubernetes in Docker Desktop (macOS):"
  err "  1. Open Docker Desktop."
  err "  2. Go to Settings -> Kubernetes."
  err "  3. Enable 'Enable Kubernetes' and click 'Apply & Restart'."
  err "  4. Wait until the Kubernetes status indicator turns green (first start may take several minutes)."
  err "  5. Verify with: kubectl --context $KUBECTL_CTX cluster-info"
  err "Then re-run this script."
}
