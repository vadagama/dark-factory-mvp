#!/usr/bin/env bash
# T-043 increment 1 pilot driver: local CLI access to the factory state store.
#
# What this script solves (docs/plan-t043-e2e-pilot.md, increment 1):
#   1. PostgreSQL port-forward for the CLI - the operator's laptop reaches the
#      in-cluster factory-postgres through `kubectl port-forward` and a
#      rewritten DATABASE_URL (host -> 127.0.0.1). Secrets come from the repo
#      .env via the environment and are never echoed.
#   2. Workspace environment for agent stages (local mirror + worktree root).
#   3. Intake / advance / status commands for the 10-task pilot run.
#
# Port note: the plan nominally says 127.0.0.1:5432, but on this machine
# 127.0.0.1:5432 is occupied by a Docker Desktop listener (com.docker), which
# answers with an authentication failure for the factory user. The forward
# therefore binds 127.0.0.1:55432 by default (override: PILOT_PG_LOCAL_PORT).
#
# Usage:
#   deploy/local/pilot/increment-1.sh pg-forward start|stop|status
#   deploy/local/pilot/increment-1.sh env-check
#   deploy/local/pilot/increment-1.sh doctor
#   deploy/local/pilot/increment-1.sh intake
#   deploy/local/pilot/increment-1.sh status [change-id]
#   deploy/local/pilot/increment-1.sh advance <change-id>
#   deploy/local/pilot/increment-1.sh advance-all
#
# Exit codes of `advance` mirror the CLI contract (cli.md): 0 advanced or
# replayed, 10 waiting (human gate / CI), 20 blocked, 1 error, 2 invalid input.
# `advance-all` treats 10/20 as normal stops and fails only on 1/2.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
PG_LOCAL_PORT="${PILOT_PG_LOCAL_PORT:-55432}"
PF_PID_FILE="${TMPDIR:-/tmp}/df-pilot-pg-forward.pid"
PF_LOG_FILE="${TMPDIR:-/tmp}/df-pilot-pg-forward.log"
PY="${PILOT_PYTHON:-uv run python}"

# Names only, never values: every variable the pilot run needs (AGENTS.md:
# secrets pass through the environment, the agent never reads .env content).
REQUIRED_VARS=(
  DATABASE_URL
  DARK_FACTORY_API_TOKENS
  DARK_FACTORY_LLM_BASE_URL
  DARK_FACTORY_LLM_API_KEY
  DARK_FACTORY_LLM_MODEL
  DARK_FACTORY_GITHUB_APP_ID
  DARK_FACTORY_GITHUB_APP_PRIVATE_KEY
  DARK_FACTORY_GITHUB_INSTALLATION_ID
)

log() { printf '[pilot] %s\n' "$*"; }
die() { printf '[pilot] ERROR: %s\n' "$*" >&2; exit 2; }

# --- environment ---------------------------------------------------------

load_env() {
  local env_file="${REPO_ROOT}/.env"
  [ -f "${env_file}" ] || die ".env not found in the repo root (${REPO_ROOT})"
  # shellcheck disable=SC1090
  set -a
  . "${env_file}"
  set +a
}

local_database_url() {
  # Rewrite the cluster-internal host of DATABASE_URL to the local forward.
  # The pattern touches only the host segment; the credential part is printed
  # nowhere.
  printf '%s' "${DATABASE_URL}" | sed -E "s/@[^/@]+:[0-9]+\//@127.0.0.1:${PG_LOCAL_PORT}\//"
}

pilot_env() {
  load_env
  DATABASE_URL="$(local_database_url)" || die "cannot rewrite DATABASE_URL"
  export DATABASE_URL
  case "${DATABASE_URL}" in
    *@127.0.0.1:"${PG_LOCAL_PORT}"/*) ;;
    *) die "DATABASE_URL does not match the expected cluster-URL shape" ;;
  esac
  export DARK_FACTORY_WORKSPACE_ROOT="${PILOT_WORKSPACE_ROOT:-${HOME}/Documents/GitHub/df-workspaces}"
  export DARK_FACTORY_WORKSPACE_MIRROR_ROOT="${PILOT_WORKSPACE_MIRROR_ROOT:-${HOME}/Documents/GitHub/df-mirrors}"
  mkdir -p "${DARK_FACTORY_WORKSPACE_ROOT}"
}

# --- subcommands ---------------------------------------------------------

cmd_pg_forward() {
  local action="${1:-}"
  case "${action}" in
    start)
      if port_is_listening; then
        log "port-forward already listening on 127.0.0.1:${PG_LOCAL_PORT}"
        return 0
      fi
      rm -f "${PF_PID_FILE}"
      kubectl -n factory port-forward "svc/factory-postgres" "${PG_LOCAL_PORT}:5432" \
        >>"${PF_LOG_FILE}" 2>&1 &
      printf '%s\n' "$!" >"${PF_PID_FILE}"
      wait_for_port
      log "port-forward up: 127.0.0.1:${PG_LOCAL_PORT} -> svc/factory-postgres:5432 (pid $(cat "${PF_PID_FILE}"))"
      ;;
    stop)
      if [ -f "${PF_PID_FILE}" ]; then
        kill "$(cat "${PF_PID_FILE}")" 2>/dev/null || true
        rm -f "${PF_PID_FILE}"
        log "port-forward stopped"
      else
        log "no pid file; nothing to stop"
      fi
      ;;
    status)
      if port_is_listening; then
        log "listening on 127.0.0.1:${PG_LOCAL_PORT}"
      else
        log "not listening on 127.0.0.1:${PG_LOCAL_PORT}"
        return 1
      fi
      ;;
    *)
      die "usage: pg-forward start|stop|status"
      ;;
  esac
}

port_is_listening() {
  nc -z 127.0.0.1 "${PG_LOCAL_PORT}" >/dev/null 2>&1
}

wait_for_port() {
  local attempt
  for attempt in $(seq 1 15); do
    if port_is_listening; then
      return 0
    fi
    sleep 1
  done
  die "port-forward did not come up on 127.0.0.1:${PG_LOCAL_PORT}; see ${PF_LOG_FILE}"
}

cmd_env_check() {
  pilot_env
  local var
  local missing=0
  for var in "${REQUIRED_VARS[@]}"; do
    if [ -n "${!var:-}" ]; then
      log "ok      ${var}"
    else
      log "MISSING ${var}"
      missing=1
    fi
  done
  log "workspace root: ${DARK_FACTORY_WORKSPACE_ROOT}"
  log "mirror root:    ${DARK_FACTORY_WORKSPACE_MIRROR_ROOT}"
  if [ "${missing}" -ne 0 ]; then
    die "required environment variables are missing (see above)"
  fi
  log "environment ok (values are not shown)"
}

cmd_doctor() {
  pilot_env
  cmd_pg_forward start
  ${PY} -c 'import psycopg  # noqa: F401' || die "psycopg import failed; run: uv sync"
  uv run factory doctor --json
}

cmd_intake() {
  pilot_env
  cmd_pg_forward start >/dev/null
  ${PY} "${SCRIPT_DIR}/pilot_api.py" intake "${SCRIPT_DIR}/tasks.json"
}

cmd_status() {
  pilot_env
  ${PY} "${SCRIPT_DIR}/pilot_api.py" status "$@"
}

cmd_advance() {
  [ $# -ge 1 ] || die "usage: advance <change-id>"
  pilot_env
  cmd_pg_forward start >/dev/null
  local rc=0
  uv run factory run advance --change-id "$1" --json || rc=$?
  case "${rc}" in
    0) log "advance ${1}: stage advanced (exit 0)" ;;
    10) log "advance ${1}: waiting - human gate or CI (exit 10)" ;;
    20) log "advance ${1}: blocked - needs operator attention (exit 20)" ;;
    1) log "advance ${1}: error (exit 1)" ;;
    2) log "advance ${1}: invalid input / unreachable store (exit 2)" ;;
  esac
  return "${rc}"
}

cmd_advance_all() {
  pilot_env
  cmd_pg_forward start >/dev/null
  local ids
  ids="$(${PY} -c 'import json,sys; print(" ".join(t["id"] for t in json.load(open(sys.argv[1]))["tasks"]))' \
    "${SCRIPT_DIR}/tasks.json")"
  local id rc failures=0 summary=""
  for id in ${ids}; do
    rc=0
    uv run factory run advance --change-id "${id}" --json >/dev/null 2>&1 || rc=$?
    case "${rc}" in
      0) summary="${summary}${id}=advanced" ;;
      10) summary="${summary}${id}=waiting" ;;
      20) summary="${summary}${id}=blocked" ;;
      1|2) summary="${summary}${id}=ERROR(${rc})" ; failures=$((failures + 1)) ;;
    esac
    summary="${summary}\n"
  done
  printf '%b' "${summary}"
  if [ "${failures}" -ne 0 ]; then
    log "advance-all finished with ${failures} error(s)"
    return 1
  fi
  log "advance-all finished"
}

main() {
  [ $# -ge 1 ] || die "usage: increment-1.sh <command> [args]; commands: pg-forward, env-check, doctor, intake, status, advance, advance-all"
  local command="${1}"
  shift
  case "${command}" in
    pg-forward) cmd_pg_forward "$@" ;;
    env-check) cmd_env_check ;;
    doctor) cmd_doctor ;;
    intake) cmd_intake "$@" ;;
    status) cmd_status "$@" ;;
    advance) cmd_advance "$@" ;;
    advance-all) cmd_advance_all "$@" ;;
    *) die "unknown command: ${command}" ;;
  esac
}

main "$@"
