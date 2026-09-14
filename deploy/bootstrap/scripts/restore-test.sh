#!/usr/bin/env bash
# Automated restore test (T029): restores the latest `factory` database dump
# into a temporary database (restore_test_<ts>), compares the table set and
# per-table row counts against the source, then drops the temporary database.
# Requires at least one backup dump on the backups PVC: trigger the backup
# CronJob first on a fresh install. Exits non-zero on any mismatch.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# shellcheck source-path=SCRIPTDIR
source "$ROOT_DIR/lib/common.sh"

require_cmds kubectl

NS="factory"
POD_NAME="restore-test-runner"
MANIFEST="$ROOT_DIR/manifests/restore-test.yaml"

if ! cluster_reachable; then
  k8s_disabled_hint
  exit 1
fi

cleanup() { kctl -n "$NS" delete pod "$POD_NAME" --ignore-not-found >/dev/null 2>&1 || true; }
trap cleanup EXIT

kctl -n "$NS" delete pod "$POD_NAME" --ignore-not-found --wait=false >/dev/null
kctl -n "$NS" apply -f "$MANIFEST" >/dev/null

if ! kctl -n "$NS" wait --for=jsonpath='{.status.phase}'=Succeeded "pod/$POD_NAME" --timeout=300s >/dev/null 2>&1; then
  err "restore test pod did not succeed:"
  kctl -n "$NS" get pod "$POD_NAME" || true
  kctl -n "$NS" logs "$POD_NAME" --all-containers --prefix --tail=80 || true
  die "restore test failed (pod did not complete successfully)"
fi

logs="$(kctl -n "$NS" logs "$POD_NAME" --all-containers 2>/dev/null | grep 'RESTORE_TEST' || true)"
info "restore test output:"
printf '%s\n' "$logs" | sed 's/^/  /'
result="$(printf '%s' "$logs" | sed -n 's/.*result=\([A-Z]*\).*/\1/p' | tail -n 1)"
case "$result" in
  PASS) ok "restore test passed (dump restored into a temporary database and verified)" ;;
  SKIP) warn "restore test skipped inside the pod: no backup dumps found yet (trigger the backup CronJob first)" ;;
  *) die "restore test failed (result=${result:-missing})" ;;
esac
