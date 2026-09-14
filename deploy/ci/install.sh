#!/usr/bin/env bash
# Install the CI runner tier (T030): RBAC for agent jobs, the ephemeral
# self-hosted GitHub Actions runner Deployment in `ci`, the scripts ConfigMap
# and the narrowed egress NetworkPolicy for `factory-runs`.
#
# Secrets are NEVER created here (nothing secret lives in git): the GitHub App
# credentials secret `factory-github-app` must exist in namespace `ci` before
# or after the install — without it the runner pod starts, prints the exact
# remediation command and retries (CreateContainerConfigError is avoided on
# purpose via secretRef.optional).
#
# Prerequisites: deploy/bootstrap (T029) already applied — namespaces `ci` and
# `factory-runs` with the service accounts `factory-ci` / `factory-agent`.
#
# Usage: install.sh [--context NAME]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR" && pwd)"

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

# --- Prerequisites from the bootstrap (T029) ---------------------------------
for ns in ci factory-runs; do
  kctl get namespace "$ns" >/dev/null 2>&1 || die "namespace '$ns' not found — run deploy/bootstrap/bootstrap.sh first"
done
kctl -n ci get serviceaccount factory-ci >/dev/null 2>&1 \
  || die "service account 'factory-ci' not found in namespace 'ci' — run deploy/bootstrap/bootstrap.sh first"
kctl -n factory-runs get serviceaccount factory-agent >/dev/null 2>&1 \
  || die "service account 'factory-agent' not found in namespace 'factory-runs' — run deploy/bootstrap/bootstrap.sh first"

# --- GitHub App secret (created outside git, never by this script) -----------
if ! kctl -n ci get secret factory-github-app >/dev/null 2>&1; then
  warn "secret 'factory-github-app' does not exist in namespace 'ci'."
  warn "The runner pod will keep retrying with a clear error until you create it (outside git):"
  warn "  kubectl -n ci create secret generic factory-github-app \\"
  warn "    --from-literal=DARK_FACTORY_GITHUB_APP_ID=<numeric app id> \\"
  warn "    --from-literal=DARK_FACTORY_GITHUB_INSTALLATION_ID=<numeric installation id> \\"
  warn "    --from-literal=DARK_FACTORY_GITHUB_APP_PRIVATE_KEY=\$(cat app-private-key.pem)"
  warn "The GitHub App needs the 'Administration' repository permission (write) to mint runner registration tokens."
  warn "Continuing with the install; see deploy/ci/README.md for details."
fi

# --- Idempotent apply ---------------------------------------------------------
# The scripts ConfigMap is built from the real files (kept version-controlled
# and linted); the dry-run|apply pipeline keeps the step idempotent.
info "creating ConfigMap factory-runner-scripts from deploy/ci/scripts/{entrypoint,run-agent-job}.sh"
kctl -n ci create configmap factory-runner-scripts \
  --from-file=entrypoint.sh="$SCRIPT_DIR/scripts/entrypoint.sh" \
  --from-file=run-agent-job.sh="$SCRIPT_DIR/scripts/run-agent-job.sh" \
  --from-file=agent-job-template.yaml="$ROOT_DIR/manifests/agent-job-template.yaml" \
  --dry-run=client -o yaml | kctl apply -f -

info "applying RBAC (factory-runs Role + cross-namespace RoleBinding)"
kctl apply -f "$ROOT_DIR/manifests/rbac.yaml"

info "applying narrowed egress NetworkPolicy for factory-runs (public HTTPS only)"
kctl apply -f "$ROOT_DIR/manifests/networkpolicies.yaml"

info "applying runner Deployment factory-runner in namespace ci"
kctl apply -f "$ROOT_DIR/manifests/runner-deployment.yaml"

info "waiting for the runner pod (180s; skips to a warning if the GitHub App secret is still missing)"
if ! kctl -n ci rollout status deployment/factory-runner --timeout=180s; then
  warn "runner Deployment is not ready yet. Pod status and logs:"
  kctl -n ci get pods -l app.kubernetes.io/name=factory-runner || true
  kctl -n ci logs deployment/factory-runner --tail=20 || true
  warn "This is expected while the GitHub App secret is missing or invalid."
fi

ok "install complete. Next steps:"
ok "  1. ./verify.sh --context $KUBECTL_CTX   # security/RBAC/TTL verification"
ok "  2. trigger a workflow with runs-on: [self-hosted, factory] (wiring is T031)"
