#!/usr/bin/env bash
# Capacity smoke check for the single-node Docker Desktop cluster (T029, ADR-010).
# Measures node allocatable resources, idle usage (when metrics-server is
# available) and the peak memory/CPU of one e2e-like probe job, then compares
# the result against the 10-12GB Docker Desktop profile. Re-run before raising
# job concurrency.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# shellcheck source-path=SCRIPTDIR
source "$ROOT_DIR/lib/common.sh"

require_cmds kubectl jq awk sleep

PROFILE_MIN_MEM_MIB=10240 # ADR-010 profile: 10-12GB for the whole Docker VM
JOB_NS="factory-runs"
JOB_NAME="capacity-smoke-e2e"

mem_to_mib() {
  printf '%s' "$1" | awk '{
    v = $1
    if (v ~ /Ki$/) { sub(/Ki$/, "", v); printf "%.0f", v / 1024 }
    else if (v ~ /Mi$/) { sub(/Mi$/, "", v); printf "%.0f", v }
    else if (v ~ /Gi$/) { sub(/Gi$/, "", v); printf "%.0f", v * 1024 }
    else { printf "%.0f", v / 1048576 }
  }'
}

cleanup() { kctl -n "$JOB_NS" delete job "$JOB_NAME" --ignore-not-found >/dev/null 2>&1 || true; }
trap cleanup EXIT

if ! cluster_reachable; then
  k8s_disabled_hint
  exit 1
fi

node_name="$(kctl get nodes -o json | jq -r '.items[0].metadata.name')"
alloc_cpu="$(kctl get node "$node_name" -o json | jq -r '.status.allocatable.cpu')"
alloc_mem="$(kctl get node "$node_name" -o json | jq -r '.status.allocatable.memory')"
alloc_mem_mib="$(mem_to_mib "$alloc_mem")"
ok "node $node_name allocatable: cpu=$alloc_cpu memory=$alloc_mem (~${alloc_mem_mib}Mi)"

if kctl top nodes >/dev/null 2>&1; then
  info "idle usage (kubectl top):"
  kctl top node "$node_name" || true
  kctl top pods --all-namespaces --sort-by=memory | head -6 || true
else
  warn "metrics-server is not available; idle usage numbers are skipped"
fi

info "running one e2e-like probe job (busybox, ~20s cgroup sampling)..."
cleanup
kctl apply -f "$ROOT_DIR/manifests/capacity-test-job.yaml" >/dev/null
if ! kctl -n "$JOB_NS" wait --for=condition=complete "job/$JOB_NAME" --timeout=240s >/dev/null 2>&1; then
  kctl -n "$JOB_NS" get pods -l job-name="$JOB_NAME" || true
  kctl -n "$JOB_NS" logs "job/$JOB_NAME" --all-containers --prefix --tail=50 || true
  die "capacity probe job did not complete"
fi
result="$(kctl -n "$JOB_NS" logs "job/$JOB_NAME" --all-containers 2>/dev/null | grep 'CAPACITY_RESULT' | tail -n 1)"
if [[ -z "$result" ]]; then
  die "capacity probe job produced no CAPACITY_RESULT line"
fi
peak_bytes="$(printf '%s' "$result" | sed -n 's/.*mem_peak_kernel_bytes=\([0-9]*\).*/\1/p')"
cpu_cores="$(printf '%s' "$result" | sed -n 's/.*cpu_avg_cores=\([0-9.]*\).*/\1/p')"
if [[ -z "$peak_bytes" || -z "$cpu_cores" ]]; then
  die "cannot parse capacity probe result: $result"
fi
peak_mib=$(((peak_bytes + 1048575) / 1048576))

printf '\n'
printf 'Capacity summary (profile: %dMi+ Docker VM, concurrency=1)\n' "$PROFILE_MIN_MEM_MIB"
printf '  node allocatable memory : %s (~%sMi)\n' "$alloc_mem" "$alloc_mem_mib"
printf '  node allocatable cpu    : %s\n' "$alloc_cpu"
printf '  probe job peak memory   : %sMi (limit 512Mi)\n' "$peak_mib"
printf '  probe job average cpu   : %s cores (limit 0.5)\n' "$cpu_cores"
printf '\n'

if [[ "$alloc_mem_mib" -ge "$PROFILE_MIN_MEM_MIB" ]]; then
  ok "verdict: headroom available for concurrency=1 (re-run this smoke before raising concurrency, ADR-010)"
elif [[ "$alloc_mem_mib" -ge 8192 ]]; then
  warn "verdict: allocatable memory is below the 10-12GB profile - raise Docker Desktop resources before increasing load"
else
  die "verdict: no headroom - allocatable memory ${alloc_mem_mib}Mi is too low for the factory profile (need >=10Gi)"
fi
