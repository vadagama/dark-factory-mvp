#!/usr/bin/env bash
# Run one factory stage as an ephemeral agent Job in `factory-runs` (T030).
#
# This script runs INSIDE the runner pod (namespace `ci`) as a workflow step.
# It renders manifests/agent-job-template.yaml, creates the Job via the
# Kubernetes REST API (curl only — the runner image has no kubectl), waits for
# completion, fetches the pod logs, extracts the StageResult between the
# __FACTORY_STAGE_RESULT_BEGIN__ / __FACTORY_STAGE_RESULT_END__ markers into
# ./stage_result.json (the Actions workspace) and maps the agent container exit
# code per the cli.md contract (mirrors .github/workflows/factory-stage.yml):
#
#   exit 0/10 -> script exits 0 (succeeded / waiting is not an error)
#   exit 20/1/2 -> script exits with the same code (job fails)
#
# Kubernetes access: the pod's projected service-account token (SA
# `factory-ci`), bounded by the factory-runs Role (manifests/rbac.yaml).
# The agent job pod itself carries no credentials (automount: false).
#
# Usage:
#   run-agent-job.sh --stage construction [--change ./fixtures/chg_smoke.yaml]
#                    [--route quick] [--input-revision N] [--run-id ID]
#                    [--sha SHA] [--repo-url URL] [--image DIGEST]
#                    [--job-name NAME] [--timeout SECONDS] [--delete]
#                    [--workdir DIR]
#
# Normal cleanup is TTL-driven (ttlSecondsAfterFinished, T030 DoD); --delete
# removes the Job immediately (debugging/CI hygiene).
set -euo pipefail

log() { printf '[run-agent-job] %s\n' "$*"; }
fail() { printf '[run-agent-job] error: %s\n' "$*" >&2; exit 2; }

: "${KUBE_API_SERVER:=https://kubernetes.default.svc}"
: "${KUBE_CA_FILE:=/mnt/kube-ca/ca.crt}"
: "${KUBE_TOKEN_FILE:=/mnt/sa-token/token}"
: "${FACTORY_JOB_NAMESPACE:=factory-runs}"
: "${TEMPLATE_PATH:=/mnt/scripts/agent-job-template.yaml}"
# Placeholder until T033 publishes the factory image with an immutable digest
# (docs T-044); override per call with --image.
: "${FACTORY_AGENT_IMAGE:=ghcr.io/vadagama/dark-factory@sha256:0000000000000000000000000000000000000000000000000000000000000000}"
: "${FACTORY_REPO_URL:=https://github.com/vadagama/dark-factory-mvp}"
: "${FACTORY_GIT_SHA:=}"
DEADLINE_SECONDS="${FACTORY_JOB_DEADLINE_SECONDS:-3600}"
TTL_SECONDS="${FACTORY_JOB_TTL_SECONDS:-3600}"
POLL_INTERVAL="${FACTORY_JOB_POLL_INTERVAL:-5}"

stage="" change="" route="" input_revision="" run_id="" sha=""
job_name="" timeout=0 delete=false workdir="$PWD"

usage() { grep '^# Usage:' -A 6 "$0" | sed 's/^# \{0,1\}//'; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --stage) stage="${2:-}"; shift 2 ;;
    --change) change="${2:-}"; shift 2 ;;
    --route) route="${2:-}"; shift 2 ;;
    --input-revision) input_revision="${2:-}"; shift 2 ;;
    --run-id) run_id="${2:-}"; shift 2 ;;
    --sha) sha="${2:-}"; shift 2 ;;
    --repo-url) repo_url="${2:-}"; shift 2 ;;
    --image) image="${2:-}"; shift 2 ;;
    --job-name) job_name="${2:-}"; shift 2 ;;
    --timeout) timeout="${2:-}"; shift 2 ;;
    --workdir) workdir="${2:-}"; shift 2 ;;
    --delete) delete=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) fail "unknown argument: $1 (see --help)" ;;
  esac
done
repo_url="${repo_url:-$FACTORY_REPO_URL}"
image="${image:-$FACTORY_AGENT_IMAGE}"
sha="${sha:-$FACTORY_GIT_SHA}"

[[ -n "$stage" ]] || { usage; fail "--stage is required"; }
[[ -n "$sha" ]] || fail "--sha is required (the stage runs on the final SHA of the change, FR-009/SC-004)"
for tool in curl jq awk; do
  command -v "$tool" >/dev/null 2>&1 || fail "missing tool: $tool"
done
[[ -r "$TEMPLATE_PATH" ]] || fail "job template is not readable: $TEMPLATE_PATH"
[[ -r "$KUBE_TOKEN_FILE" ]] || fail "Kubernetes token file is not readable: $KUBE_TOKEN_FILE"
[[ -r "$KUBE_CA_FILE" ]] || fail "Kubernetes CA file is not readable: $KUBE_CA_FILE"

# RFC1123-31115 sub-domain safe job name: lowercase alphanumerics, '-', '.';
# starts with a letter, at most 63 chars.
sanitize() { printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | sed -e 's/[^a-z0-9.-]/-/g' -e 's/^-*//' -e 's/-*$//'; }
short_id="$(sanitize "${run_id:-$(date +%Y%m%d%H%M%S)}")"
short_id="${short_id:0:8}"
stage_s="$(sanitize "$stage")"
job_name="$(sanitize "${job_name:-factory-agent-$stage_s-$short_id}")"
[[ -n "$job_name" ]] || fail "empty job name"
job_name="${job_name:0:63}"

rendered="$(mktemp)"
trap 'rm -f "$rendered"' EXIT

awk \
  -v JOB_NAME="$job_name" \
  -v RUN_ID="${run_id:-$job_name}" \
  -v STAGE="$stage" \
  -v CHANGE_PATH="${change:-}" \
  -v ROUTE="${route:-}" \
  -v INPUT_REVISION="${input_revision:-}" \
  -v GIT_SHA="$sha" \
  -v REPO_URL="$repo_url" \
  -v IMAGE="$image" \
  -v DEADLINE_SECONDS="$DEADLINE_SECONDS" \
  -v TTL_SECONDS="$TTL_SECONDS" \
  '
  {
    gsub(/__JOB_NAME__/, JOB_NAME)
    gsub(/__RUN_ID__/, RUN_ID)
    gsub(/__STAGE__/, STAGE)
    gsub(/__CHANGE_PATH__/, CHANGE_PATH)
    gsub(/__ROUTE__/, ROUTE)
    gsub(/__INPUT_REVISION__/, INPUT_REVISION)
    gsub(/__GIT_SHA__/, GIT_SHA)
    gsub(/__REPO_URL__/, REPO_URL)
    gsub(/__IMAGE__/, IMAGE)
    gsub(/__DEADLINE_SECONDS__/, DEADLINE_SECONDS)
    gsub(/__TTL_SECONDS__/, TTL_SECONDS)
    print
  }' "$TEMPLATE_PATH" > "$rendered"

api_call() {
  # api_call METHOD PATH [DATA_FILE] -> body on stdout, HTTP code on stderr
  local method="$1" path="$2" data="${3:-}" body
  local args=(-sS --max-time 30 --cacert "$KUBE_CA_FILE"
    -H "Authorization: Bearer $(cat "$KUBE_TOKEN_FILE")"
    -X "$method" "$KUBE_API_SERVER$path")
  if [[ -n "$data" ]]; then
    args+=(-H 'Content-Type: application/yaml' --data-binary "@$data")
  fi
  body="$(curl "${args[@]}")" || fail "request to the Kubernetes API failed ($method $path)"
  printf '%s' "$body"
}

# 1. Create the Job -----------------------------------------------------------
body="$(api_call POST "/apis/batch/v1/namespaces/$FACTORY_JOB_NAMESPACE/jobs" "$rendered")"
kind="$(printf '%s' "$body" | jq -r '.kind // empty')"
if [[ "$kind" != "Job" ]]; then
  reason="$(printf '%s' "$body" | jq -r '.message // "unparseable error response"' | head -c 400)"
  fail "job creation rejected: $reason"
fi
log "job created: $FACTORY_JOB_NAMESPACE/$job_name (deadline ${DEADLINE_SECONDS}s, ttl ${TTL_SECONDS}s)"

# 2. Wait for completion ------------------------------------------------------
deadline=$(( $(date +%s) + (timeout > 0 ? timeout : DEADLINE_SECONDS + 120) ))
job_json=""
while :; do
  job_json="$(api_call GET "/apis/batch/v1/namespaces/$FACTORY_JOB_NAMESPACE/jobs/$job_name")"
  done_type="$(printf '%s' "$job_json" | jq -r '.status.conditions // [] | map(select(.status == "True") | .type) | .[0] // empty')"
  if [[ "$done_type" == "Complete" || "$done_type" == "Failed" ]]; then
    break
  fi
  if [[ "$(date +%s)" -ge "$deadline" ]]; then
    log "timed out waiting for job $job_name (it will be cleaned up by TTL)"
    exit 2
  fi
  sleep "$POLL_INTERVAL"
done

# 3. Fetch the agent pod, its logs and the container exit code ----------------
pod_name="$(
  curl -sSG --max-time 30 --cacert "$KUBE_CA_FILE" \
    -H "Authorization: Bearer $(cat "$KUBE_TOKEN_FILE")" \
    "$KUBE_API_SERVER/api/v1/namespaces/$FACTORY_JOB_NAMESPACE/pods" \
    --data-urlencode "labelSelector=job-name=$job_name" \
    | jq -r '.items | map(.metadata.name) | .[0] // empty'
)"
if [[ -z "$pod_name" ]]; then
  fail "no pod found for job $job_name"
fi

logs="$(api_call GET "/api/v1/namespaces/$FACTORY_JOB_NAMESPACE/pods/$pod_name/log")"
printf '%s\n' "$logs"

exit_code="$(
  curl -sS --max-time 30 --cacert "$KUBE_CA_FILE" \
    -H "Authorization: Bearer $(cat "$KUBE_TOKEN_FILE")" \
    "$KUBE_API_SERVER/api/v1/namespaces/$FACTORY_JOB_NAMESPACE/pods/$pod_name" \
    | jq -r '
        [.status.containerStatuses // []
         | map(.state.terminated.exitCode // empty) | .[0]] as $codes
        | if ($codes | length) > 0 then $codes[0]
          elif (.status.phase // "") == "Failed" then 1
          elif (.status.phase // "") == "Succeeded" then 0
          else 1 end'
)"

# 4. Extract the StageResult between the log markers --------------------------
result_file="$workdir/stage_result.json"
printf '%s' "$logs" \
  | awk '/__FACTORY_STAGE_RESULT_BEGIN__/{flag=1; next} /__FACTORY_STAGE_RESULT_END__/{flag=0} flag' \
  > "$result_file"
if [[ -s "$result_file" ]] && jq -e . "$result_file" >/dev/null 2>&1; then
  log "StageResult extracted to $result_file"
else
  log "no parseable StageResult between the markers (status/exit code below are authoritative)"
  : > "$result_file"
fi

# 5. Optional immediate cleanup (normal cleanup is TTL) ------------------------
if [[ "$delete" == "true" ]]; then
  api_call DELETE "/apis/batch/v1/namespaces/$FACTORY_JOB_NAMESPACE/jobs/$job_name" >/dev/null
  log "job deleted: $FACTORY_JOB_NAMESPACE/$job_name"
fi

# 6. Exit-code mapping (cli.md contract) --------------------------------------
status="$(jq -r '.status // empty' "$result_file" 2>/dev/null || true)"
if [[ "$exit_code" == "0" || "$exit_code" == "10" ]]; then
  log "job succeeded: exit=$exit_code status=${status:-unknown} pod=$pod_name"
  exit 0
fi
log "job failed: exit=$exit_code status=${status:-unknown} pod=$pod_name"
exit "$exit_code"
