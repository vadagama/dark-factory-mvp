#!/usr/bin/env bash
# Verify the CI runner tier (T030) against a LIVE cluster: RBAC matrix for the
# runner service account (impersonated), a hardened probe job created AS the
# runner SA (live test of the cross-namespace RoleBinding), the negative
# security checks from inside the pod (no root, read-only rootfs, no SA token,
# no Kubernetes API access, no credentials in the environment) and the
# TTL-cleanup DoD (the probe job uses a shortened TTL and must disappear).
#
# The probe job manifest is rendered from manifests/agent-job-probe.yaml with
# the cluster's actual Kubernetes service ClusterIP.
#
# Usage: verify.sh [--context NAME] [--keep-probe]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck source-path=SCRIPTDIR
source "$SCRIPT_DIR/scripts/common.sh"

require_cmds kubectl jq

KEEP_PROBE=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --context) KUBECTL_CTX="${2:-}"; shift 2 ;;
    --keep-probe) KEEP_PROBE=true; shift ;;
    -h|--help) grep '^# ' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

RUNNER_SA="system:serviceaccount:ci:factory-ci"
NS="factory-runs"
PROBE_TEMPLATE="$SCRIPT_DIR/manifests/agent-job-probe.yaml"
PROBE_NAME="factory-agent-probe-$(date +%H%M%S)"
PROBE_DEADLINE=300
PROBE_TTL=60

if ! cluster_reachable; then
  k8s_disabled_hint
  exit 1
fi

overall=0
fail_test() { err "FAIL: $*"; overall=1; }
pass_test() { ok "PASS: $*"; }

# ---------------------------------------------------------------------------
# 1. Static/live posture of the runner Deployment
# ---------------------------------------------------------------------------
info "checking runner Deployment posture"
if ! kctl -n ci get deployment factory-runner >/dev/null 2>&1; then
  fail_test "Deployment factory-runner not found in namespace ci (run install.sh)"
else
  automount="$(kctl -n ci get deployment factory-runner -o jsonpath='{.spec.template.spec.automountServiceAccountToken}')"
  sa="$(kctl -n ci get deployment factory-runner -o jsonpath='{.spec.template.spec.serviceAccountName}')"
  image="$(kctl -n ci get deployment factory-runner -o jsonpath='{.spec.template.spec.containers[0].image}')"
  replicas="$(kctl -n ci get deployment factory-runner -o jsonpath='{.spec.replicas}')"
  if [[ "$automount" == "false" ]]; then
    pass_test "runner pod automountServiceAccountToken=false"
  else
    fail_test "runner pod automountServiceAccountToken=$automount (must be false)"
  fi
  if [[ "$sa" == "factory-ci" ]]; then
    pass_test "runner pod uses service account factory-ci"
  else
    fail_test "runner pod uses SA '$sa' (must be factory-ci)"
  fi
  if [[ "$image" == *@sha256:* ]]; then
    pass_test "runner image is digest-pinned"
  else
    fail_test "runner image is not digest-pinned: $image"
  fi
  if [[ "$replicas" == "1" ]]; then
    pass_test "runner replicas=1 (concurrency=1, ADR-010)"
  else
    warn "runner replicas=$replicas (profile is 1)"
  fi
  pod_phase="$(kctl -n ci get pods -l app.kubernetes.io/name=factory-runner -o jsonpath='{range .items[*]}{.status.phase}{"\n"}{end}' | head -n 1 || true)"
  if [[ "${pod_phase:-}" == "Running" ]]; then
    pass_test "runner pod is Running (registered or registering)"
  else
    warn "runner pod phase is '${pod_phase:-absent}' — expected until the GitHub App secret exists and is valid"
  fi
fi

# ---------------------------------------------------------------------------
# 2. RBAC matrix for the runner SA (impersonation, no pod needed)
# ---------------------------------------------------------------------------
info "checking RBAC matrix for $RUNNER_SA"
can() { kctl auth can-i "$1" "$2" ${3:+-n "$3"} --as="$RUNNER_SA" >/dev/null 2>&1; }
expect() { # expect yes|no verb resource [namespace] description
  local want="$1" verb="$2" res="$3" ns="${4:-}" desc="$5" got
  if can "$verb" "$res" "$ns"; then got=yes; else got=no; fi
  if [[ "$got" == "$want" ]]; then
    pass_test "$desc"
  else
    fail_test "$desc (expected $want, got $got)"
  fi
}
expect yes create jobs "$NS" "runner SA may create agent Jobs in $NS"
expect yes get jobs "$NS" "runner SA may get Jobs in $NS"
expect yes delete jobs "$NS" "runner SA may delete Jobs in $NS"
expect yes get pods "$NS" "runner SA may get pods in $NS"
expect yes get pods/log "$NS" "runner SA may read pod logs in $NS"
expect no create pods "$NS" "runner SA may NOT create bare pods in $NS (jobs only)"
expect no get secrets "$NS" "runner SA may NOT read secrets in $NS"
expect no create jobs ci "runner SA may NOT create jobs in ci (its own namespace)"
expect no create jobs factory "runner SA may NOT create jobs in control-plane namespace factory"
expect no create jobs argocd "runner SA may NOT create jobs in control-plane namespace argocd"
expect no get secrets factory "runner SA may NOT read secrets in control-plane namespace factory"
expect no delete namespaces "" "runner SA may NOT delete namespaces (cluster scope)"
expect no create deployments apps-dev "runner SA may NOT create deployments in apps-dev"

# ---------------------------------------------------------------------------
# 3. Live probe job: created AS the runner SA (RBAC in action)
# ---------------------------------------------------------------------------
info "rendering probe job $PROBE_NAME"
cluster_ip="$(kctl -n default get svc kubernetes -o jsonpath='{.spec.clusterIP}')"
[[ -n "$cluster_ip" ]] || { fail_test "cannot resolve the kubernetes service ClusterIP"; exit 1; }
rendered="$(mktemp)"
cleanup() {
  if [[ "$KEEP_PROBE" == "true" ]]; then
    warn "keeping probe job $PROBE_NAME (--keep-probe); it will be removed by TTL"
  else
    kctl -n "$NS" delete job "$PROBE_NAME" --ignore-not-found --wait=false >/dev/null 2>&1 || true
  fi
  rm -f "$rendered"
}
trap cleanup EXIT

sed -e "s/__JOB_NAME__/$PROBE_NAME/" \
  -e "s/__KUBERNETES_CLUSTER_IP__/$cluster_ip/" \
  -e "s/__DEADLINE_SECONDS__/$PROBE_DEADLINE/" \
  -e "s/__TTL_SECONDS__/$PROBE_TTL/" \
  "$PROBE_TEMPLATE" > "$rendered"

info "creating probe job AS $RUNNER_SA (live RoleBinding test)"
if ! kctl -n "$NS" create --as="$RUNNER_SA" -f "$rendered" >/dev/null 2>&1; then
  fail_test "probe job creation AS $RUNNER_SA failed (RoleBinding broken?)"
  exit 1
fi
pass_test "probe job created with the runner SA's own credentials"

info "waiting up to ${PROBE_DEADLINE}s for the probe job to finish"
probe_ok=false
elapsed=0
while [[ "$elapsed" -lt "$PROBE_DEADLINE" ]]; do
  state="$(kctl -n "$NS" get job "$PROBE_NAME" -o json 2>/dev/null \
    | jq -r '.status.conditions // [] | map(select(.status == "True") | .type) | .[0] // empty')"
  if [[ "$state" == "Complete" ]]; then probe_ok=true; break; fi
  if [[ "$state" == "Failed" ]]; then break; fi
  sleep 5; elapsed=$((elapsed + 5))
done
if [[ "$probe_ok" != "true" ]]; then
  fail_test "probe job did not complete successfully (state=${state:-none})"
  kctl -n "$NS" get job "$PROBE_NAME" -o wide || true
  kctl -n "$NS" get pods --selector=job-name="$PROBE_NAME" || true
  kctl -n "$NS" logs "job/$PROBE_NAME" --all-containers --prefix --tail=50 || true
  exit 1
fi
pass_test "probe job completed (exit 0 from inside the hardened pod)"

logs="$(kctl -n "$NS" logs "job/$PROBE_NAME" --all-containers --prefix 2>/dev/null | grep 'PROBE_RESULT' || true)"
if [[ -z "$logs" ]]; then
  fail_test "no PROBE_RESULT lines found in probe logs"
  exit 1
fi
value_of() { printf '%s' "$logs" | sed -n "s/.*$1=\([A-Z]*\).*/\1/p" | head -n 1; }
for check in uid_65532 rootfs_readonly tmp_writable no_sa_token env_clean dns api_blocked api_ip_blocked external_https http80_blocked; do
  value="$(value_of "$check")"
  if [[ "$value" == "PASS" ]]; then
    pass_test "in-pod check $check"
  else
    fail_test "in-pod check $check (${value:-missing})"
  fi
done

# ---------------------------------------------------------------------------
# 4. TTL cleanup DoD: the probe job must disappear by TTL
# ---------------------------------------------------------------------------
info "waiting up to $((PROBE_TTL + 180))s for TTL cleanup of the probe job (ttlSecondsAfterFinished=$PROBE_TTL)"
ttl_ok=false
wait_total=$((PROBE_TTL + 180))
elapsed=0
while [[ "$elapsed" -lt "$wait_total" ]]; do
  if ! kctl -n "$NS" get job "$PROBE_NAME" >/dev/null 2>&1; then
    ttl_ok=true
    break
  fi
  sleep 10; elapsed=$((elapsed + 10))
done
if [[ "$ttl_ok" == "true" ]]; then
  pass_test "TTL cleanup: job $PROBE_NAME (and its pod) deleted by ttlSecondsAfterFinished"
else
  fail_test "TTL cleanup: job $PROBE_NAME still present after ${wait_total}s (TTL controller enabled?)"
fi

if [[ "$overall" -ne 0 ]]; then
  die "verification failed (see FAIL lines above)"
fi
ok "all T030 verification checks passed"
