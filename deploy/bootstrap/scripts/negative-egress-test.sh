#!/usr/bin/env bash
# Negative egress tests for the baseline NetworkPolicy (T029).
# For each tested namespace a probe pod must: resolve DNS, reach HTTPS 443
# (allowed), fail to reach HTTP 80 / TCP 25 (blocked by deny-by-default) and,
# in `factory`, reach the PostgreSQL service (intra-namespace allow).
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

if ! cluster_reachable; then
  k8s_disabled_hint
  exit 1
fi

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
  for value in "$https443" "$http80" "$port25" "$dns"; do
    if [[ "$value" != "PASS" ]]; then
      status="FAIL"
    fi
  done
  if [[ "$ns" == "factory" && "$pg" != "PASS" ]]; then
    status="FAIL"
  fi
  if [[ "$status" == "PASS" ]]; then
    ok "negative egress tests passed in namespace $ns (dns=$dns https443=$https443 http80_blocked=$http80 port25_blocked=$port25 postgres=$pg)"
  else
    err "negative egress tests FAILED in namespace $ns (dns=${dns:-?} https443=${https443:-?} http80_blocked=${http80:-?} port25_blocked=${port25:-?} postgres=${pg:-?})"
    overall=1
  fi
  kctl -n "$ns" delete pod "$PROBE_NAME" --ignore-not-found --wait >/dev/null
done

if [[ "$overall" -ne 0 ]]; then
  die "negative egress tests failed"
fi
