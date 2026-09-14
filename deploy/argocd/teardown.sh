#!/usr/bin/env bash
# Remove the Argo CD tier (T032) and return to the bootstrap baseline (T029):
#   1. delete ALL Applications in namespace `argocd` (root apps + any children
#      created by the apps-dev app-of-apps). Application deletion cascades to
#      the managed workloads, so the factory chart release in `factory` and the
#      pilot workloads in `apps-dev` are removed as well;
#   2. helm uninstall the argocd release and the local Applications chart.
#
# Intentionally KEPT (user-managed or regenerable-by-design): namespace `argocd`
# (per T032 scope), the Application CRDs (crds.keep=true) and
# dark-factory-gitops-repo-creds. The helm-managed argocd-secret is removed
# with the release — a re-install generates a NEW admin password (the stale
# argocd-initial-admin-secret from the previous install is deleted here).
#
# Usage: teardown.sh [--context NAME]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck source-path=SCRIPTDIR
source "$SCRIPT_DIR/scripts/common.sh"

require_cmds kubectl helm

while [[ $# -gt 0 ]]; do
  case "$1" in
    --context) KUBECTL_CTX="${2:-}"; shift 2 ;;
    -h|--help) grep '^# ' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

if ! cluster_reachable; then
  k8s_disabled_hint
  exit 1
fi

existing="$(kctl -n argocd get applications --no-headers --ignore-not-found 2>/dev/null | awk '{print $1}' || true)"
if [[ -n "$existing" ]]; then
  info "deleting Applications (cascades to the workloads they manage):"
  echo "${existing//$'\n'/$'\n  - '}"
  # cascading=true (kubectl default) removes the managed resources in
  # `factory` and `apps-dev` — that is exactly the return to the T029 baseline.
  kctl -n argocd delete applications --all --wait=false
else
  info "no Applications to delete"
fi

info "uninstalling the root Applications chart (dark-factory-argocd-apps)"
helm --namespace argocd uninstall dark-factory-argocd-apps 2>/dev/null \
  || info "release dark-factory-argocd-apps not found"

info "uninstalling Argo CD (chart release argocd; CRDs are kept, crds.keep=true)"
helm --namespace argocd uninstall argocd 2>/dev/null \
  || info "release argocd not found"

info "deleting the stale argocd-initial-admin-secret (regenerated on the next install)"
kctl -n argocd delete secret argocd-initial-admin-secret --ignore-not-found

ok "teardown complete: namespace argocd and dark-factory-gitops-repo-creds are kept (user-managed),"
ok "CRDs remain installed; re-running install.sh restores the tier (new admin password)"
