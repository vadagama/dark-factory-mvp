#!/usr/bin/env bash
# Tear down the local dark factory Kubernetes environment (T029).
# DESTRUCTIVE: deletes the platform namespaces including PVC data
# (PostgreSQL data, backups) and generated secrets. Irreversible.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck source-path=SCRIPTDIR
source "$ROOT_DIR/lib/common.sh"

NAMESPACES=(argocd factory ci factory-runs apps-dev)
ASSUME_YES=false

usage() {
  cat <<'EOF'
Usage: teardown.sh [--context NAME] [--yes]

Deletes the dark factory platform namespaces (argocd, factory, ci,
factory-runs, apps-dev) with all their resources: workloads, quotas,
network policies, PVCs (PostgreSQL data and backups) and generated secrets.
This is irreversible: database data and generated credentials are lost.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --context)
      if [[ $# -lt 2 ]]; then
        usage >&2
        die "option $1 requires a value"
      fi
      KUBECTL_CTX="$2"
      shift 2
      ;;
    --yes) ASSUME_YES=true; shift ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      die "unknown option: $1"
      ;;
  esac
done

require_cmds kubectl jq docker
if ! cluster_reachable; then
  k8s_disabled_hint
  exit 1
fi

warn "This will DELETE namespaces: ${NAMESPACES[*]}"
warn "All data is lost: PostgreSQL data and backup PVCs, generated credentials, quotas, policies."
if [[ "$ASSUME_YES" != "true" ]]; then
  read -r -p "Type DELETE to confirm: " reply
  if [[ "$reply" != "DELETE" ]]; then
    info "aborted by user"
    exit 0
  fi
fi

for ns in "${NAMESPACES[@]}"; do
  if kctl get namespace "$ns" >/dev/null 2>&1; then
    kctl delete namespace "$ns" --wait >/dev/null
    ok "namespace $ns deleted"
  else
    info "namespace $ns not found (skipped)"
  fi
done
ok "teardown complete"
