#!/usr/bin/env bash
# Remove the CI runner tier (T030) and restore the bootstrap network baseline:
# deleting the narrowed `allow-egress-https` policy in `factory-runs` would
# leave the namespace with NO HTTPS egress at all (policies are additive), so
# teardown re-applies the bootstrap networkpolicies.yaml (broad 0.0.0.0/0:443,
# T029 state).
#
# Secrets (factory-github-app) are NOT deleted — they are user-managed and
# never lived in git.
#
# Usage: teardown.sh [--context NAME]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BOOTSTRAP_MANIFESTS_DIR="$(cd "$SCRIPT_DIR/../bootstrap/manifests" && pwd)"

# shellcheck source-path=SCRIPTDIR
source "$SCRIPT_DIR/scripts/common.sh"

require_cmds kubectl

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

info "deleting runner Deployment factory-runner (namespace ci)"
kctl -n ci delete deployment factory-runner --ignore-not-found

info "deleting ConfigMap factory-runner-scripts (namespace ci)"
kctl -n ci delete configmap factory-runner-scripts --ignore-not-found

info "deleting RBAC factory-agent-jobs (Role in factory-runs + RoleBinding)"
kctl -n factory-runs delete rolebinding factory-agent-jobs --ignore-not-found
kctl -n factory-runs delete role factory-agent-jobs --ignore-not-found

info "restoring the bootstrap NetworkPolicy baseline in factory-runs"
kctl apply -f "$BOOTSTRAP_MANIFESTS_DIR/networkpolicies.yaml"

ok "teardown complete: factory-runs egress is back to the T029 bootstrap baseline"
ok "note: secret factory-github-app in namespace ci was intentionally kept (user-managed)"
