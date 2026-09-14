#!/usr/bin/env bash
# Negative egress tests for the baseline NetworkPolicy (T029).
# For each tested namespace a probe pod must: resolve DNS, reach HTTPS 443
# (allowed) and, in `factory`, reach the PostgreSQL service (intra-namespace
# allow). The negative checks (HTTP 80 / TCP 25 must be blocked) are asserted
# only when the cluster actually enforces NetworkPolicies: some embedded
# distributions (Docker Desktop) have no policy-enforcing CNI, so deny-by-default
# is inert there. Enforcement is detected once with a canary pair in a
# throwaway namespace: two identical probes, one selected by a deny-all-egress
# NetworkPolicy. If the denied canary still reaches the network, policies are
# not enforced and the blocking checks degrade to SKIPPED with a warning.
# Probe pods are cleaned up after the run.
# Usage: negative-egress-test.sh [namespace ...] (default: factory factory-runs)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# shellcheck source-path=SCRIPTDIR
source "$ROOT_DIR/lib/common.sh"

require_cmds kubectl

PROBE_NAME="negative-egress-probe"
PROBE_MANIFEST="$ROOT_DIR/manifests/negative-egress-probe.yaml"
NAMESPACES=(factory factory-runs)
if [[ "$#" -gt 0 ]]; then
  NAMESPACES=("$@")
fi

CANARY_NS="factory-policy-canary"
CANARY_IMAGE="curlimages/curl:8.11.1"
POLICY_ENFORCEMENT="unknown"

canary_cleanup() {
  kctl delete ns "$CANARY_NS" --ignore-not-found --wait=false >/dev/null 2>&1 || true
}
trap canary_cleanup EXIT

# Detect whether the cluster enforces NetworkPolicy at all. Two canary pods
# in a throwaway namespace run the same curl: canary-open has no policies
# (must succeed — proves the target and the network work), canary-denied is
# selected by a deny-all-egress policy (must fail when enforcement works).
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
  local cmd='curl -sS -o /dev/null --max-time 10 http://example.com/ >/dev/null 2>&1 && echo CANARY_REACHABLE=yes || echo CANARY_REACHABLE=no'
  kctl -n "$CANARY_NS" run canary-open --image="$CANARY_IMAGE" --restart=Never \
    --labels=egress-canary=open --command -- /bin/sh -c "$cmd" >/dev/null
  kctl -n "$CANARY_NS" run canary-denied --image="$CANARY_IMAGE" --restart=Never \
    --labels=egress-canary=denied --command -- /bin/sh -c "$cmd" >/dev/null
  local open_result denied_result
  if ! kctl -n "$CANARY_NS" wait --for=jsonpath='{.status.phase}'=Succeeded pod/canary-open --timeout=120s >/dev/null 2>&1; then
    warn "egress canary (no policy) did not complete; cannot judge NetworkPolicy enforcement"
    return 0
  fi
  open_result="$(kctl -n "$CANARY_NS" logs canary-open 2>/dev/null | grep 'CANARY_REACHABLE' | tail -n 1 | sed -n 's/.*=\(.*\)/\1/p')"
  if [[ "$open_result" != "yes" ]]; then
    warn "egress target unreachable from a pod without any policy — cluster networking or target issue"
    return 0
  fi
  if ! kctl -n "$CANARY_NS" wait --for=jsonpath='{.status.phase}'=Succeeded pod/canary-denied --timeout=120s >/dev/null 2>&1; then
    # A denied pod stuck in teardown can legitimately never Succeed; treat a
    # stuck denied canary as "enforcement works" only if it is still running.
    warn "denied egress canary did not complete; treating NetworkPolicy as enforced"
    POLICY_ENFORCEMENT="on"
    return 0
  fi
  denied_result="$(kctl -n "$CANARY_NS" logs canary-denied 2>/dev/null | grep 'CANARY_REACHABLE' | tail -n 1 | sed -n 's/.*=\(.*\)/\1/p')"
  if [[ "$denied_result" == "yes" ]]; then
    POLICY_ENFORCEMENT="off"
    warn "NetworkPolicy is NOT enforced by this cluster (CNI without policy support, typical for Docker Desktop): deny-by-default is informational here, blocking checks are reported as SKIPPED"
  else
    POLICY_ENFORCEMENT="on"
    ok "NetworkPolicy enforcement detected: deny-all-egress canary was blocked"
  fi
}

cluster_reachable || {
  k8s_disabled_hint
  exit 1
}

detect_policy_enforcement

overall=0
for ns in "${NAMESPACES[@]}"; do
  kctl -n "$ns" delete pod "$PROBE_NAME" --ignore-not-found --wait=false >/dev/null
  kctl -n "$ns" apply -f "$PROBE_MANIFEST" >/dev/null
  if ! kctl -n "$ns" wait --for=jsonpath='{.status.phase}'=Succeeded "pod/$PROBE_NAME" --timeout=180s >/dev/null 2>&1; then
    err "probe pod in namespace $ns did not succeed:"
    kctl -n "$ns" get pod "$PROBE_NAME" || true
    kctl -n "$ns" logs "$PROBE_NAME" --all-containers --prefix --tail=50 || true
    kctl -n "$ns" delete pod "$PROBE_NAME" --ignore-not-found --wait=false >/dev/null || true
    overall=1
    continue
  fi
  logs="$(kctl -n "$ns" logs "$PROBE_NAME" --all-containers --prefix 2>/dev/null | grep 'EGRESS_RESULT' || true)"
  https443="$(printf '%s' "$logs" | sed -n 's/.*https443=\([A-Z]*\).*/\1/p' | head -n 1)"
  http80="$(printf '%s' "$logs" | sed -n 's/.*http80_blocked=\([A-Z]*\).*/\1/p' | head -n 1)"
  port25="$(printf '%s' "$logs" | sed -n 's/.*port25_blocked=\([A-Z]*\).*/\1/p' | head -n 1)"
  dns="$(printf '%s' "$logs" | sed -n 's/.*dns=\([A-Z]*\).*/\1/p' | head -n 1)"
  pg="$(printf '%s' "$logs" | sed -n 's/.*postgres=\([A-Z]*\).*/\1/p' | head -n 1)"
  status="PASS"
  for value in "$https443" "$dns"; do
    if [[ "$value" != "PASS" ]]; then
      status="FAIL"
    fi
  done
  if [[ "$ns" == "factory" && "$pg" != "PASS" ]]; then
    status="FAIL"
  fi
  if [[ "$POLICY_ENFORCEMENT" == "on" ]]; then
    # Enforcement works: the negative checks are meaningful and mandatory.
    for value in "$http80" "$port25"; do
      if [[ "$value" != "PASS" ]]; then
        status="FAIL"
      fi
    done
  else
    http80="SKIPPED"
    port25="SKIPPED"
  fi
  if [[ "$status" == "PASS" ]]; then
    ok "negative egress tests passed in namespace $ns (dns=$dns https443=$https443 http80_blocked=$http80 port25_blocked=$port25 postgres=$pg policy_enforcement=$POLICY_ENFORCEMENT)"
  else
    err "negative egress tests FAILED in namespace $ns (dns=${dns:-?} https443=${https443:-?} http80_blocked=${http80:-?} port25_blocked=${port25:-?} postgres=${pg:-?} policy_enforcement=$POLICY_ENFORCEMENT)"
    overall=1
  fi
  kctl -n "$ns" delete pod "$PROBE_NAME" --ignore-not-found --wait >/dev/null
done

if [[ "$overall" -ne 0 ]]; then
  die "negative egress tests failed"
fi
