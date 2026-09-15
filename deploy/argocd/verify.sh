#!/usr/bin/env bash
# Verify the Argo CD tier (T032) against a LIVE cluster:
#   1. every Argo CD workload (label app.kubernetes.io/part-of=argocd) exists
#      and is ready — the core set (server, repo-server, application-controller
#      statefulset, redis) is required, extra chart components are checked too;
#   2. both root Applications are registered with automated syncPolicy;
#   3. Application health/sync states are reported; expected environment gaps
#      (GitOps repo not bootstrapped yet, factory image not published yet,
#      missing database secret) degrade to WARN with the exact remediation,
#      they do not fail the verification — the missing pieces are created by
#      humans/tasks outside T032 (T033 publishes the image; the seed push is
#      documented in gitops-seed/README.md).
#
# Usage: verify.sh [--context NAME]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

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

overall=0
fail_test() { err "FAIL: $*"; overall=1; }
pass_test() { ok "PASS: $*"; }
# WARN marks an expected environment gap with remediation, never a script failure.
warn_test() { warn "WARN: $*"; }

# ---------------------------------------------------------------------------
# 1. Argo CD workloads are present and ready
# ---------------------------------------------------------------------------
info "checking Argo CD workloads in namespace argocd"
workloads_json="$(kctl -n argocd get deployments,statefulsets \
  -l app.kubernetes.io/part-of=argocd -o json 2>/dev/null || echo '{}')"

for core in deploy/argocd-server deploy/argocd-repo-server deploy/argocd-redis sts/argocd-application-controller; do
  if kctl -n argocd get "$core" >/dev/null 2>&1; then
    pass_test "core workload present: $core"
  else
    fail_test "core workload missing: $core (run install.sh)"
  fi
done

count="$(printf '%s' "$workloads_json" | jq -r '.items // [] | length')"
if [[ "$count" -eq 0 ]]; then
  fail_test "no Argo CD workloads found (label app.kubernetes.io/part-of=argocd) — run install.sh"
else
  info "readiness of all $count Argo CD workloads:"
  while read -r row; do
    name="${row%%|*}"; ready="${row#*|}"
    if [[ "$ready" == "True" ]]; then
      pass_test "workload $name ready"
    else
      fail_test "workload $name NOT ready (ready=$ready)"
    fi
  done <<EOF
$(printf '%s' "$workloads_json" | jq -r '.items[] | [.metadata.name, ([.status.conditions[]? | select(.type == "Available") | .status][0] // "False")] | join("|")')
EOF
fi

# ---------------------------------------------------------------------------
# 2. Root Applications registered with automated sync
# ---------------------------------------------------------------------------
app_field() { # app_field <name> <jsonpath> <default>
  kctl -n argocd get application "$1" -o "jsonpath=$2" 2>/dev/null || printf '%s' "$3"
}

for app in factory apps-dev; do
  info "checking Application '$app'"
  if ! kctl -n argocd get application "$app" >/dev/null 2>&1; then
    fail_test "Application $app not found in namespace argocd (run install.sh)"
    continue
  fi
  pass_test "Application $app is registered"
  if [[ "$(app_field "$app" '{.spec.syncPolicy.automated}' missing)" != "missing" ]]; then
    pass_test "Application $app has automated syncPolicy (ADR-011: dev deploy after merge is automatic)"
  else
    fail_test "Application $app has no automated syncPolicy"
  fi
  echo "  sync:   $(app_field "$app" '{.status.sync.status}' 'unknown')"
  echo "  health: $(app_field "$app" '{.status.health.status}' 'unknown')"
  message="$(app_field "$app" '{.status.conditions[*].message}' '')"
  [[ -n "$message" ]] && echo "  conditions: $message"
done

# ---------------------------------------------------------------------------
# 3. Expected environment gaps -> WARN with remediation (not FAIL)
# ---------------------------------------------------------------------------
# 3a. GitOps repository: root of the apps-dev app-of-apps.
if ! kctl -n argocd get secret dark-factory-gitops-repo-creds >/dev/null 2>&1; then
  warn_test "no repo-creds secret for the GitOps repository (fine while it is public or absent):"
  warn_test "  private repo -> kubectl -n argocd create secret generic dark-factory-gitops-repo-creds \\"
  warn_test "    --from-literal=type=git --from-literal=url=<gitops-repo-url> \\"
  warn_test "    --from-literal=username=<user> --from-literal=password=<read-only PAT>"
  warn_test "  then label it: argocd.argoproj.io/secret-type=repository"
else
  pass_test "repo-creds secret for the GitOps repository is present"
fi
apps_dev_health="$(app_field apps-dev '{.status.health.status}' '')"
if [[ "$apps_dev_health" == "Degraded" || "$apps_dev_health" == "Unknown" || "$apps_dev_health" == "" ]]; then
  warn_test "Application apps-dev is not Healthy ($apps_dev_health) — expected until the GitOps"
  warn_test "  repository exists: push the seed from deploy/argocd/gitops-seed/ (gitops-seed/README.md)."
fi

# 3b. Platform application: image placeholder until T033 + database secret.
factory_health="$(app_field factory '{.status.health.status}' '')"
if [[ "$factory_health" == "Degraded" || "$factory_health" == "Progressing" || "$factory_health" == "" ]]; then
  warn_test "Application factory is not Healthy ($factory_health) — expected while the platform"
  warn_test "  image is the bootstrap placeholder (published in T033)."
fi
if ! kctl -n factory get secret factory-api-database >/dev/null 2>&1; then
  warn_test "secret 'factory-api-database' missing in namespace factory (migrations/API will fail):"
  warn_test "  kubectl -n factory create secret generic factory-api-database \\"
  warn_test "    --from-literal=DATABASE_URL='postgresql+psycopg://factory:<password>@factory-postgres:5432/factory'"
fi

# ---------------------------------------------------------------------------
# 4. Child applications overview (app-of-apps result) — informational
# ---------------------------------------------------------------------------
children="$(kctl -n argocd get applications \
  -o custom-columns='NAME:.metadata.name,SYNC:.status.sync.status,HEALTH:.status.health.status' 2>/dev/null \
  | tail -n +2 | grep -v -E '^(factory|apps-dev)( |$)' || true)"
if [[ -n "$children" ]]; then
  info "child applications (from the apps-dev app-of-apps):"
  echo "${children//$'\n'/$'\n  '}"
else
  info "no child applications yet (expected until the GitOps repository is bootstrapped)"
fi

if [[ "$overall" -ne 0 ]]; then
  die "verification failed (see FAIL lines above)"
fi
ok "all T032 verification checks passed"
