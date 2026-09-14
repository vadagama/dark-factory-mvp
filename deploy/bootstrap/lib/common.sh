#!/usr/bin/env bash
# Shared helpers for deploy/bootstrap scripts (T029, ADR-010).
# shellcheck shell=bash source-path=SCRIPTDIR

set -euo pipefail

: "${KUBECTL_CTX:=docker-desktop}"
: "${DOCKER_DESKTOP_SETTINGS:=${HOME}/Library/Group Containers/group.com.docker/settings-store.json}"

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

docker_running() { docker info >/dev/null 2>&1; }

# Prints an actionable message when the cluster API is unreachable, most
# commonly because Kubernetes is disabled in Docker Desktop.
k8s_disabled_hint() {
  err "Kubernetes API of cluster '$KUBECTL_CTX' is not reachable (kubectl cluster-info failed)."
  if [[ -f "$DOCKER_DESKTOP_SETTINGS" ]]; then
    local enabled
    # jq's alternative operator treats a legitimate `false` as missing, so
    # read the value via has() and stringify it explicitly.
    enabled="$(jq -r 'if has("KubernetesEnabled") then (.KubernetesEnabled | tostring) else "absent" end' "$DOCKER_DESKTOP_SETTINGS" 2>/dev/null || echo unreadable)"
    if [[ "$enabled" == "false" ]]; then
      err "Docker Desktop settings store confirms KubernetesEnabled=false."
    fi
  fi
  err "To enable Kubernetes in Docker Desktop (macOS):"
  err "  1. Open Docker Desktop."
  err "  2. Go to Settings -> Kubernetes."
  err "  3. Enable 'Enable Kubernetes' and click 'Apply & Restart'."
  err "  4. Wait until the Kubernetes status indicator turns green (first start may take several minutes)."
  err "  5. Verify with: kubectl --context $KUBECTL_CTX cluster-info"
  err "Then re-run this script."
}
