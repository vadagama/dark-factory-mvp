#!/usr/bin/env bash
# Verify the CI runner tier (T030) against a LIVE cluster: RBAC matrix for the
# runner service account (impersonated), a hardened probe job created AS the
# runner SA (live test of the cross-namespace RoleBinding), the negative
# security checks from inside the pod (no root, read-only rootfs, no SA token,
# no Kubernetes API access, no credentials in the environment) and the
# TTL-cleanup DoD (the probe job uses a shortened TTL and must disappear).
#
# Before creating the probe, a canary pair (ported from the bootstrap
# negative-egress-test.sh, TD-001) detects whether the cluster actually
# enforces NetworkPolicies. On a cluster without enforcement (typical Docker
# Desktop) the probe's network negative checks (api_blocked, api_ip_blocked,
# http80_blocked) degrade to SKIPPED with a warning; positive and credential
# checks stay hard in either case.
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
# busybox:1.37, the same digest-pinned image the probe itself runs (cached on
# the node), so the canary adds no new supply-chain surface.
CANARY_NS="factory-policy-canary"
CANARY_IMAGE="busybox@sha256:9db7b59979c38555a39def84a31fb98b5296952f9e3afd4f6f11f05b07adfab0"
POLICY_ENFORCEMENT="unknown"

if ! cluster_reachable; then
  k8s_disabled_hint
  exit 1
fi

overall=0
fail_test() { err "FAIL: $*"; overall=1; }
pass_test() { ok "PASS: $*"; }

# Throwaway namespace with a deny-all-egress canary pair (see header). Removed
# on exit together with the probe job and the rendered manifest.
cleanup() {
  kctl delete ns "$CANARY_NS" --ignore-not-found --wait=false >/dev/null 2>&1 || true
  if [[ "$KEEP_PROBE" == "true" ]]; then
    warn "keeping probe job $PROBE_NAME (--keep-probe); it will be removed by TTL"
  else
    kctl -n "$NS" delete job "$PROBE_NAME" --ignore-not-found --wait=false >/dev/null 2>&1 || true
  fi
  rm -f "${rendered:-}"
}
trap cleanup EXIT

detect_policy_enforcement() {
  # Wait for a full deletion: a previous run's trap deletes the namespace
  # asynchronously, and creating over a Terminating namespace fails.
  kctl delete ns "$CANARY_NS" --ignore-not-found --wait=true >/dev/null 2>&1 || true
  kctl create ns "$CANARY_NS" >/dev/null
  kctl -n "$CANARY_NS" apply -f - >/dev/null <<'YAML'
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: canary-deny-egress
spec:
  podSelector:
    matchLabels:
      egress-canary: denied
  policyTypes:
    - Egress
  egress: []
YAML
  local cmd='wget -q -T 10 -O /dev/null http://example.com/ >/dev/null 2>&1 && echo CANARY_REACHABLE=yes || echo CANARY_REACHABLE=no'
  kctl -n "$CANARY_NS" run canary-open --image="$CANARY_IMAGE" --restart=Never \
    --labels=egress-canary=open --command -- /bin/sh -c "$cmd" >/dev/null
  kctl -n "$CANARY_NS" run canary-denied --image="$CANARY_IMAGE" --restart=Never \
    --labels=egress-canary=denied --command -- /bin/sh -c "$cmd" >/dev/null
  local open_result denied_result
  if ! kctl -n "$CANARY_NS" wait --for=jsonpath='{.status.phase}'=Succeeded pod/canary-open --timeout=120s >/dev/null 2>&1; then
    warn "egress canary (no policy) did not complete; cannot judge NetworkPolicy enforcement — network negative checks will be SKIPPED"
    return 0
  fi
  open_result="$(kctl -n "$CANARY_NS" logs canary-open 2>/dev/null | grep 'CANARY_REACHABLE' | tail -n 1 | sed -n 's/.*=\(.*\)/\1/p' || true)"
  if [[ "$open_result" != "yes" ]]; then
    warn "egress target unreachable from a pod without any policy — cluster networking or target issue; network negative checks will be SKIPPED"
    return 0
  fi
  if ! kctl -n "$CANARY_NS" wait --for=jsonpath='{.status.phase}'=Succeeded pod/canary-denied --timeout=120s >/dev/null 2>&1; then
    # A denied pod stuck in teardown can legitimately never Succeed; treat a
    # stuck denied canary as "enforcement works" (same semantics as the
    # bootstrap negative-egress-test.sh canary).
    warn "denied egress canary did not complete; treating NetworkPolicy as enforced"
    POLICY_ENFORCEMENT="on"
    return 0
  fi
  denied_result="$(kctl -n "$CANARY_NS" logs canary-denied 2>/dev/null | grep 'CANARY_REACHABLE' | tail -n 1 | sed -n 's/.*=\(.*\)/\1/p' || true)"
  if [[ "$denied_result" == "yes" ]]; then
    POLICY_ENFORCEMENT="off"
    warn "NetworkPolicy is NOT enforced by this cluster (CNI without policy support, typical for Docker Desktop — TD-001): the probe's network negative checks degrade to SKIPPED"
  else
    POLICY_ENFORCEMENT="on"
    ok "NetworkPolicy enforcement detected: deny-all-egress canary was blocked"
  fi
}

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
info "detecting NetworkPolicy enforcement with a canary pair (TD-001)"
detect_policy_enforcement
net_mode="skip"
if [[ "$POLICY_ENFORCEMENT" == "on" ]]; then net_mode="assert"; fi

info "rendering probe job $PROBE_NAME"
cluster_ip="$(kctl -n default get svc kubernetes -o jsonpath='{.spec.clusterIP}')"
[[ -n "$cluster_ip" ]] || { fail_test "cannot resolve the kubernetes service ClusterIP"; exit 1; }
rendered="$(mktemp)"
sed -e "s/__JOB_NAME__/$PROBE_NAME/" \
  -e "s/__KUBERNETES_CLUSTER_IP__/$cluster_ip/" \
  -e "s/__DEADLINE_SECONDS__/$PROBE_DEADLINE/" \
  -e "s/__TTL_SECONDS__/$PROBE_TTL/" \
  -e "s/__NET_NEGATIVE_MODE__/$net_mode/" \
  "$PROBE_TEMPLATE" > "$rendered"

info "creating probe job AS $RUNNER_SA (live RoleBinding test)"
if ! kctl -n "$NS" create --as="$RUNNER_SA" -f "$rendered" >/dev/null 2>&1; then
  fail_test "probe job creation AS $RUNNER_SA failed (RoleBinding broken?)"
  exit 1
fi
pass_test "probe job created with the runner SA's own credentials"

info "waiting up to ${PROBE_DEADLINE}s for the probe job to finish"
# Terminal state comes from the succeeded/failed counters, NOT from
# conditions: newer Kubernetes (1.31+) adds interim FailureTarget/SuccessTarget
# conditions that are True before the final Failed/Complete condition lands.
# Every kubectl call here is guarded — with `set -euo pipefail` an unguarded
# failing pipeline (job already TTL-deleted, transient API error) would kill
# the script silently instead of reporting a failure.
probe_ok=false
elapsed=0
state="none"
while [[ "$elapsed" -lt "$PROBE_DEADLINE" ]]; do
  job_json="$(kctl -n "$NS" get job "$PROBE_NAME" -o json 2>/dev/null || true)"
  if [[ -z "$job_json" ]]; then
    # The job vanished before a terminal state was observed (TTL race or
    # external deletion) — its logs are gone with it, nothing to assert on.
    state="gone"
    break
  fi
  state="$(printf '%s' "$job_json" \
    | jq -r 'if (.status.succeeded // 0) > 0 then "Complete" elif (.status.failed // 0) > 0 then "Failed" else empty end')"
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
  elif [[ "$value" == "SKIPPED" ]]; then
    warn "SKIP: in-pod check $check (NetworkPolicy not enforced by this cluster — TD-001)"
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
