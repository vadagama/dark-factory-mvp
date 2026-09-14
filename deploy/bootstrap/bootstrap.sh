#!/usr/bin/env bash
# Bootstrap the local Kubernetes environment for the dark factory (T029, ADR-010).
#
# Idempotent: safe to re-run. Namespaces, quotas, service accounts, network
# policies and PostgreSQL manifests are applied declaratively; the PostgreSQL
# credentials secret is generated once (openssl rand) and kept on re-runs.
#
# Requires: Docker Desktop (with Kubernetes enabled), kubectl, jq, openssl.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MANIFESTS_DIR="$ROOT_DIR/manifests"
SCRIPTS_DIR="$ROOT_DIR/scripts"

# shellcheck source-path=SCRIPTDIR
source "$ROOT_DIR/lib/common.sh"

ONLY_LICENSE_CHECK=false
STRICT_LICENSE=false
SKIP_CAPACITY_SMOKE=false
SKIP_GITHUB_CHECK=false
SKIP_EGRESS_TESTS=false
SKIP_RESTORE_TEST=false

usage() {
  cat <<'EOF'
Usage: bootstrap.sh [options]

Bootstrap the local Docker Desktop Kubernetes environment for the dark
factory (T029, ADR-010): namespaces, quotas, service accounts, baseline
network policies, factory PostgreSQL with nightly backups, plus post-install
checks (negative egress tests, capacity smoke, GitHub App credentials).

Options:
  --context NAME         kubectl context to use (default: docker-desktop)
  --only-license-check   run only the Docker Desktop license check and exit
  --strict-license       fail when the license state cannot be determined
  --skip-capacity-smoke  skip the capacity smoke check
  --skip-github-check    skip the GitHub App credentials check
  --skip-egress-tests    skip the negative egress (NetworkPolicy) tests
  --skip-restore-test    skip the automated restore test
  -h, --help             show this help and exit

Environment:
  KUBECTL_CTX            default kubectl context (default: docker-desktop)
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --context)
      if [[ $# -lt 2 ]]; then
        usage >&2
        die "option $1 requires a value"
      fi
      KUBECTL_CTX="$2"
      shift 2
      ;;
    --only-license-check) ONLY_LICENSE_CHECK=true; shift ;;
    --strict-license) STRICT_LICENSE=true; shift ;;
    --skip-capacity-smoke) SKIP_CAPACITY_SMOKE=true; shift ;;
    --skip-github-check) SKIP_GITHUB_CHECK=true; shift ;;
    --skip-egress-tests) SKIP_EGRESS_TESTS=true; shift ;;
    --skip-restore-test) SKIP_RESTORE_TEST=true; shift ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      die "unknown option: $1"
      ;;
  esac
done

# Best-effort Docker Desktop license check (ADR-010): detect the accepted
# subscription terms; never fail the bootstrap unless --strict-license is set.
# Positive identification of the plan/edition is not possible from the
# settings store, so a manual verification hint is always printed.
check_license() {
  local state="unknown" mode="warn-only" version platform
  if [[ -f "$DOCKER_DESKTOP_SETTINGS" ]] && jq -e 'has("LicenseTermsVersion")' "$DOCKER_DESKTOP_SETTINGS" >/dev/null 2>&1; then
    state="terms-accepted"
    version="$(jq -r '.LicenseTermsVersion' "$DOCKER_DESKTOP_SETTINGS")"
    ok "Docker Desktop subscription terms accepted (LicenseTermsVersion=$version); plan/edition is not detectable automatically"
  else
    warn "Docker Desktop license state not detected (settings file or LicenseTermsVersion field missing)"
  fi
  platform="$(docker version --format '{{.Server.Platform.Name}}' 2>/dev/null || true)"
  if [[ -n "$platform" ]]; then
    info "docker server platform: $platform"
  fi
  warn "License check is best-effort: commercial use of Docker Desktop requires a paid subscription, verify it yourself (https://www.docker.com/legal/subscription/)"
  if [[ "$STRICT_LICENSE" == "true" ]]; then
    mode="strict"
    if [[ "$state" != "terms-accepted" ]]; then
      die "strict license mode: license state could not be confirmed; verify the subscription manually (https://www.docker.com/legal/subscription/)"
    fi
  fi
  ok "license check completed (state=$state, mode=$mode)"
}

step_prereq() {
  require_cmds docker kubectl jq openssl
  if ! docker_running; then
    die "Docker is not running. Start Docker Desktop and re-run this script."
  fi
  ok "docker daemon is running"
  if ! cluster_reachable; then
    k8s_disabled_hint
    exit 1
  fi
  ok "kubernetes cluster '$KUBECTL_CTX' is reachable"
}

step_apply() {
  kctl apply -f "$MANIFESTS_DIR/namespaces.yaml" >/dev/null
  ok "namespaces applied (argocd, factory, ci, factory-runs, apps-dev)"
  kctl apply -f "$MANIFESTS_DIR/quotas.yaml" >/dev/null
  ok "resource quotas and limit ranges applied (5 namespaces)"
  kctl apply -f "$MANIFESTS_DIR/serviceaccounts.yaml" >/dev/null
  ok "service accounts applied (5, automountServiceAccountToken=false)"
  kctl apply -f "$MANIFESTS_DIR/networkpolicies.yaml" >/dev/null
  ok "network policies applied (deny-by-default + explicit allows)"
}

# Generates the PostgreSQL credentials secret on the first run only; existing
# secrets are kept so repeated bootstrap runs never rotate passwords.
ensure_postgres_secret() {
  if kctl -n factory get secret factory-postgres-credentials >/dev/null 2>&1; then
    ok "secret factory/factory-postgres-credentials exists (kept, idempotent)"
    return
  fi
  local pw fpw plpw
  pw="$(openssl rand -base64 24)"
  fpw="$(openssl rand -base64 24)"
  plpw="$(openssl rand -base64 24)"
  kctl -n factory create secret generic factory-postgres-credentials \
    --from-literal=POSTGRES_PASSWORD="$pw" \
    --from-literal=FACTORY_DB_PASSWORD="$fpw" \
    --from-literal=PLANE_DB_PASSWORD="$plpw" >/dev/null
  ok "secret factory/factory-postgres-credentials generated (openssl rand; first run only, never committed)"
}

step_postgres() {
  ensure_postgres_secret
  kctl apply -f "$MANIFESTS_DIR/postgres.yaml" >/dev/null
  if ! kctl -n factory rollout status statefulset/factory-postgres --timeout=180s >/dev/null; then
    die "factory PostgreSQL did not become ready within 180s (check: kubectl --context $KUBECTL_CTX -n factory get pods)"
  fi
  ok "factory PostgreSQL ready (two logical databases: factory, plane)"
  kctl apply -f "$MANIFESTS_DIR/backup-cronjob.yaml" >/dev/null
  ok "nightly backup CronJob applied (02:30 UTC, custom-format dumps, 7-day retention, RPO 24h)"
}

run_post_install_checks() {
  export KUBECTL_CTX
  if [[ "$SKIP_EGRESS_TESTS" != "true" ]]; then
    "$SCRIPTS_DIR/negative-egress-test.sh"
  else
    warn "negative egress tests skipped (--skip-egress-tests)"
  fi
  if [[ "$SKIP_RESTORE_TEST" != "true" ]]; then
    "$SCRIPTS_DIR/restore-test.sh"
  else
    warn "restore test skipped (--skip-restore-test)"
  fi
  if [[ "$SKIP_CAPACITY_SMOKE" != "true" ]]; then
    "$SCRIPTS_DIR/capacity-smoke.sh"
  else
    warn "capacity smoke skipped (--skip-capacity-smoke)"
  fi
  if [[ "$SKIP_GITHUB_CHECK" != "true" ]]; then
    "$SCRIPTS_DIR/check-github-app.sh"
  else
    warn "GitHub App check skipped (--skip-github-check)"
  fi
}

main() {
  info "dark factory local bootstrap (T029) - context: $KUBECTL_CTX"
  step_prereq
  check_license
  if [[ "$ONLY_LICENSE_CHECK" == "true" ]]; then
    ok "license-only run finished"
    return
  fi
  step_apply
  step_postgres
  run_post_install_checks
  cat <<'EOF'
[ ok ] bootstrap complete
Next steps:
  - T030: CI runners and hardened job pods (deploy/ci/)
  - T031: Helm chart dark-factory (charts/dark-factory/)
  - T032: Argo CD installation and GitOps applications (deploy/argocd/)
  - Restore drill after the first backup: scripts/restore-test.sh
EOF
}

main
