#!/usr/bin/env bash
# Runner pod entrypoint (T030, deploy/ci/README.md).
#
# 1. Validates the GitHub App credentials (Secret `factory-github-app`).
# 2. Preflights the Kubernetes RBAC: the projected token must allow creating
#    agent Jobs in the FACTORY_JOB_NAMESPACE (fails fast with a clear message
#    instead of failing on the first workflow job).
# 3. Mints a fresh runner registration token via the GitHub App (RS256 JWT ->
#    installation access token -> registration token). Registration tokens are
#    valid for one hour; minting one per pod start is what makes the
#    --ephemeral model sustainable without manual secret rotation.
# 4. Registers an --ephemeral runner and execs run.sh: one pod = one job
#    (ADR-006). After the job the runner deregisters itself and the container
#    exits; the Deployment restarts it and the cycle repeats.
#
# The JWT pipeline mirrors deploy/bootstrap/scripts/check-github-app.sh
# (bash + openssl, no extra dependencies). Secrets are never printed; error
# messages quote GitHub's `message` field only.
set -euo pipefail

log() { printf '[runner-entrypoint] %s\n' "$*"; }
fail() { printf '[runner-entrypoint] error: %s\n' "$*" >&2; exit 1; }

GITHUB_CONFIG_URL="${GITHUB_CONFIG_URL:-}"
RUNNER_LABELS="${RUNNER_LABELS:-factory,k8s}"
APP_ID="${DARK_FACTORY_GITHUB_APP_ID:-}"
INSTALLATION_ID="${DARK_FACTORY_GITHUB_INSTALLATION_ID:-}"
PRIVATE_KEY="${DARK_FACTORY_GITHUB_APP_PRIVATE_KEY:-}"
API_URL="${DARK_FACTORY_GITHUB_API_URL:-https://api.github.com}"
API_URL="${API_URL%/}"
RUNNER_DIR="/home/runner"
: "${KUBE_API_SERVER:=https://kubernetes.default.svc}"
: "${KUBE_CA_FILE:=/mnt/kube-ca/ca.crt}"
: "${KUBE_TOKEN_FILE:=/mnt/sa-token/token}"
: "${FACTORY_JOB_NAMESPACE:=factory-runs}"

if [[ -z "$GITHUB_CONFIG_URL" ]]; then
  fail "GITHUB_CONFIG_URL is not set"
fi
if [[ -z "$APP_ID" || -z "$INSTALLATION_ID" || -z "$PRIVATE_KEY" ]]; then
  fail "GitHub App credentials are not set. Create the secret in namespace ci:
  kubectl -n ci create secret generic factory-github-app \\
    --from-literal=DARK_FACTORY_GITHUB_APP_ID=<numeric app id> \\
    --from-literal=DARK_FACTORY_GITHUB_INSTALLATION_ID=<numeric installation id> \\
    --from-literal=DARK_FACTORY_GITHUB_APP_PRIVATE_KEY=\$(cat app-private-key.pem)
  then let the Deployment restart the pod (see deploy/ci/README.md)."
fi
for tool in curl openssl jq; do
  command -v "$tool" >/dev/null 2>&1 || fail "missing tool: $tool"
done

b64url() { openssl base64 -A | sed -e 's/+/-/g' -e 's|/|_|g' -e 's/=//g'; }

build_jwt() {
  local key_file="$1" app_id="$2"
  local header payload now iat exp signing_input signature
  header="$(printf '{"alg":"RS256","typ":"JWT"}' | b64url)"
  now="$(date +%s)"
  iat=$((now - 60))
  exp=$((now + 540))
  payload="$(printf '{"iat":%d,"exp":%d,"iss":%s}' "$iat" "$exp" "$app_id" | b64url)"
  signing_input="${header}.${payload}"
  signature="$(printf '%s' "$signing_input" | openssl dgst -sha256 -sign "$key_file" -binary | b64url)"
  printf '%s.%s' "$signing_input" "$signature"
}

# ---------------------------------------------------------------------------
# 1. RBAC preflight: can the projected token create agent Jobs?
# ---------------------------------------------------------------------------
if [[ ! -r "$KUBE_TOKEN_FILE" ]]; then
  fail "Kubernetes token file is not readable: $KUBE_TOKEN_FILE"
fi
k8s_token="$(cat "$KUBE_TOKEN_FILE")"
ssar_status="$(
  curl -sS --max-time 15 --cacert "$KUBE_CA_FILE" \
    -H "Authorization: Bearer $k8s_token" -H 'Content-Type: application/json' \
    -X POST "$KUBE_API_SERVER/apis/authorization.k8s.io/v1/selfsubjectaccessreviews" \
    -d '{"apiVersion":"authorization.k8s.io/v1","kind":"SelfSubjectAccessReview","spec":{"resourceAttributes":{"group":"batch","resource":"jobs","verb":"create","namespace":"'"$FACTORY_JOB_NAMESPACE"'"}}}' \
    | jq -r '.status.allowed'
)" || fail "SelfSubjectAccessReview call failed"
if [[ "$ssar_status" != "true" ]]; then
  fail "the runner service account may NOT create jobs in namespace '$FACTORY_JOB_NAMESPACE' (RBAC not installed? run deploy/ci/install.sh)"
fi
log "RBAC preflight ok: jobs may be created in namespace $FACTORY_JOB_NAMESPACE"

# ---------------------------------------------------------------------------
# 2. Mint a registration token via the GitHub App.
# ---------------------------------------------------------------------------
tmp_key="$(mktemp)"
trap 'rm -f "$tmp_key"' EXIT
chmod 600 "$tmp_key"
printf '%s' "$PRIVATE_KEY" > "$tmp_key"
if ! openssl pkey -in "$tmp_key" -noout >/dev/null 2>&1; then
  fail "DARK_FACTORY_GITHUB_APP_PRIVATE_KEY cannot be parsed by openssl"
fi

jwt="$(build_jwt "$tmp_key" "$APP_ID")"
install_token="$(
  curl -sS --max-time 30 \
    -X POST "$API_URL/app/installations/$INSTALLATION_ID/access_tokens" \
    -H 'Accept: application/vnd.github+json' \
    -H "Authorization: Bearer $jwt" \
    | jq -r '.token // empty'
)" || fail "request to $API_URL failed (network or TLS error)"
if [[ -z "$install_token" || "$install_token" == "null" ]]; then
  fail "could not obtain an installation access token (check app id, installation id, key and that the app is installed; the response is not printed)"
fi

repo_path="${GITHUB_CONFIG_URL#*://}"
repo_path="${repo_path#*/}"          # strip the host (github.com or GHES)
repo_path="${repo_path%/}"
case "$repo_path" in
  */*) : ;;
  *) fail "GITHUB_CONFIG_URL does not look like a repository URL: $GITHUB_CONFIG_URL" ;;
esac

runner_token="$(
  curl -sS --max-time 30 \
    -X POST "$API_URL/repos/$repo_path/actions/runners/registration-token" \
    -H 'Accept: application/vnd.github+json' \
    -H "Authorization: Bearer $install_token" \
    | jq -r '.token // empty'
)" || fail "registration-token request failed"
if [[ -z "$runner_token" || "$runner_token" == "null" ]]; then
  fail "could not obtain a runner registration token for $repo_path (the GitHub App needs the Administration repository permission, write)"
fi
log "registration token minted for $repo_path (never printed)"

# ---------------------------------------------------------------------------
# 3. Register an ephemeral runner and run exactly one job.
# ---------------------------------------------------------------------------
cd "$RUNNER_DIR" || fail "runner directory $RUNNER_DIR is missing"
# The registration token is passed as an argument to config.sh; it is visible
# only to this container's own uid (ps inside the pod) and is scoped to
# registering runners. The runner name is the pod hostname and is unique per
# pod; --ephemeral makes the runner remove itself after one job.
./config.sh \
  --url "$GITHUB_CONFIG_URL" \
  --token "$runner_token" \
  --ephemeral \
  --unattended \
  --labels "$RUNNER_LABELS" \
  --work _work
log "registered; starting run.sh (one job per pod)"
exec ./run.sh
