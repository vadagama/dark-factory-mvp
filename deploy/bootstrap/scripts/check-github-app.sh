#!/usr/bin/env bash
# GitHub App credentials check (T029). Builds an RS256 JWT with openssl,
# exchanges it for an installation access token and prints only facts
# (app id, installation id, token expiry). The key and the token are never
# printed and the raw response body is never shown.
# Without credentials configured the check is skipped (exit 0).
# Usage: check-github-app.sh [--self-test]
#
# Environment:
#   DARK_FACTORY_GITHUB_APP_ID           numeric App ID
#   DARK_FACTORY_GITHUB_INSTALLATION_ID  numeric installation ID
#   DARK_FACTORY_GITHUB_APP_PRIVATE_KEY  PEM private key text
#   DARK_FACTORY_GITHUB_API_URL          optional, default https://api.github.com
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# shellcheck source-path=SCRIPTDIR
source "$ROOT_DIR/lib/common.sh"

require_cmds curl openssl jq

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

# Offline sanity check of the JWT pipeline: signs with a throwaway key and
# verifies the RS256 signature locally, without any network access.
self_test() {
  # Runs in a subshell (see caller): tmp files and the cleanup trap are
  # isolated there, so these variables are intentionally not declared local
  # — the trap must still see them at subshell exit.
  tmp_key="$(mktemp)"
  tmp_pub="$(mktemp)"
  tmp_sig="$(mktemp)"
  trap 'rm -f "$tmp_key" "$tmp_pub" "$tmp_sig"' EXIT
  chmod 600 "$tmp_key" "$tmp_pub" "$tmp_sig"
  local jwt signing_input sig_b64
  openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out "$tmp_key" 2>/dev/null
  jwt="$(build_jwt "$tmp_key" 12345)"
  signing_input="${jwt%.*}"
  sig_b64="$(printf '%s' "${jwt##*.}" | sed -e 's/-/+/g' -e 's/_/\//g')"
  case $((${#sig_b64} % 4)) in
    2) sig_b64="${sig_b64}==" ;;
    3) sig_b64="${sig_b64}=" ;;
  esac
  printf '%s' "$sig_b64" | openssl base64 -d -A > "$tmp_sig"
  openssl pkey -in "$tmp_key" -pubout -out "$tmp_pub" 2>/dev/null
  if printf '%s' "$signing_input" | openssl dgst -sha256 -verify "$tmp_pub" -signature "$tmp_sig" >/dev/null 2>&1; then
    ok "self-test passed: JWT is well-formed and its RS256 signature verifies locally"
  else
    die "self-test failed: JWT signature verification failed"
  fi
}

if [[ "${1:-}" == "--self-test" ]]; then
  # Run in a subshell so the EXIT trap fires while the local variables of
  # self_test are still in scope (temp files are removed there).
  ( self_test )
  exit
fi

app_id="${DARK_FACTORY_GITHUB_APP_ID:-}"
installation_id="${DARK_FACTORY_GITHUB_INSTALLATION_ID:-}"
private_key="${DARK_FACTORY_GITHUB_APP_PRIVATE_KEY:-}"
api_url="${DARK_FACTORY_GITHUB_API_URL:-https://api.github.com}"
api_url="${api_url%/}"

if [[ -z "$app_id" || -z "$installation_id" || -z "$private_key" ]]; then
  warn "GitHub App check skipped: set DARK_FACTORY_GITHUB_APP_ID, DARK_FACTORY_GITHUB_INSTALLATION_ID and DARK_FACTORY_GITHUB_APP_PRIVATE_KEY to enable it"
  exit 0
fi

if [[ ! "$app_id" =~ ^[0-9]+$ ]]; then
  die "DARK_FACTORY_GITHUB_APP_ID must be numeric"
fi
if [[ ! "$installation_id" =~ ^[0-9]+$ ]]; then
  die "DARK_FACTORY_GITHUB_INSTALLATION_ID must be numeric"
fi
if ! printf '%s' "$private_key" | grep -q 'BEGIN .*PRIVATE KEY'; then
  die "DARK_FACTORY_GITHUB_APP_PRIVATE_KEY does not look like a PEM private key"
fi

tmp_key="$(mktemp)"
tmp_resp="$(mktemp)"
trap 'rm -f "$tmp_key" "$tmp_resp"' EXIT
chmod 600 "$tmp_key" "$tmp_resp"
printf '%s' "$private_key" > "$tmp_key"
if ! openssl pkey -in "$tmp_key" -noout >/dev/null 2>&1; then
  die "private key cannot be parsed by openssl"
fi

jwt="$(build_jwt "$tmp_key" "$app_id")"
status="$(curl -sS --max-time 30 -o "$tmp_resp" -w '%{http_code}' \
  -X POST "$api_url/app/installations/$installation_id/access_tokens" \
  -H 'Accept: application/vnd.github+json' \
  -H "Authorization: Bearer $jwt")" || die "request to $api_url failed (network or TLS error); response is not printed"

if [[ "$status" == "201" ]]; then
  expires="$(jq -r '.expires_at // "unknown"' "$tmp_resp" 2>/dev/null || echo unknown)"
  ok "GitHub App credentials valid: app_id=$app_id installation_id=$installation_id token_expires_at=$expires"
else
  message="$(jq -r '.message // "unparseable response"' "$tmp_resp" 2>/dev/null || echo unparseable)"
  err "GitHub App installation token request failed: HTTP $status ($message)"
  err "Check the app id, installation id, key contents and that the app is installed; the response body is not printed to avoid token leakage."
  exit 1
fi
