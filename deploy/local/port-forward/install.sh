#!/usr/bin/env bash
# Install two launchd agents that keep the local port-forward tunnels alive
# (T-093):
#   dev.dark-factory.port-forward.api     svc/dark-factory          8000:8000
#   dev.dark-factory.port-forward.console svc/dark-factory-console  8080:80
#
# Why: manual `kubectl port-forward` processes die together with the Docker
# Desktop Kubernetes cluster (incident 2026-09-18) and after a reboot/login.
# launchd KeepAlive restarts kubectl whenever it exits - while the cluster is
# down it retries every ~10s, and RunAtLoad restores the tunnels at login.
#
# The install is idempotent: re-running re-renders the plists and re-bootstraps
# the agents. Stray manual forwards for the same services are stopped first to
# free the ports. The cluster does not need to be up: with it down the agents
# keep retrying in the background.
#
# Usage: [KUBECTL_CTX=<name>] install.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck source-path=SCRIPTDIR
source "$SCRIPT_DIR/scripts/common.sh"

NAMESPACE="factory"
# name|label|service|port_map
AGENTS=(
  "api|dev.dark-factory.port-forward.api|dark-factory|8000:8000"
  "console|dev.dark-factory.port-forward.console|dark-factory-console|8080:80"
)
PLIST_TEMPLATE="$SCRIPT_DIR/launchd.plist.template"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
LOG_DIR="$HOME/Library/Logs/dark-factory/port-forward"
GUI_DOMAIN="gui/$(id -u)"

require_cmds kubectl launchctl plutil curl

KUBECTL_PATH="$(command -v kubectl)"
[[ -x "$KUBECTL_PATH" ]] || die "kubectl resolved to a non-executable path: $KUBECTL_PATH"
[[ -f "$PLIST_TEMPLATE" ]] || die "template not found: $PLIST_TEMPLATE"

mkdir -p "$LAUNCH_AGENTS_DIR" "$LOG_DIR"

# --- Replace any previous install, then free the ports --------------------------
# bootout stops the launchd-owned kubectl processes; whatever still matches
# the pattern afterwards is a stray MANUAL forward holding the ports, so the
# launchd copies would fail with 'address already in use'.
for spec in "${AGENTS[@]}"; do
  IFS='|' read -r _name label _service _port_map <<<"$spec"
  launchctl bootout "$GUI_DOMAIN/$label" >/dev/null 2>&1 || true
done
if pgrep -f "kubectl.*port-forward.*svc/dark-factory" >/dev/null 2>&1; then
  warn "stopping stray manual port-forward processes for the factory services"
  pkill -f "kubectl.*port-forward.*svc/dark-factory" || true
  sleep 1
fi

# --- Cluster status: informational only -----------------------------------------
if ! kubectl --context "$KUBECTL_CTX" -n "$NAMESPACE" get svc \
  dark-factory dark-factory-console >/dev/null 2>&1; then
  warn "cluster '$KUBECTL_CTX' or the chart services are not reachable right now."
  warn "The agents will keep retrying every ~10s until it is back (this is by design)."
fi

# --- Render + bootstrap the agents ----------------------------------------------
for spec in "${AGENTS[@]}"; do
  IFS='|' read -r name label service port_map <<<"$spec"
  plist_path="$LAUNCH_AGENTS_DIR/$label.plist"
  log_path="$LOG_DIR/$name.log"

  info "rendering $plist_path ($service $port_map)"
  sed \
    -e "s|__LABEL__|$label|g" \
    -e "s|__KUBECTL_PATH__|$KUBECTL_PATH|g" \
    -e "s|__KUBECTL_CTX__|$KUBECTL_CTX|g" \
    -e "s|__NAMESPACE__|$NAMESPACE|g" \
    -e "s|__SERVICE__|$service|g" \
    -e "s|__PORT_MAP__|$port_map|g" \
    -e "s|__LOG_PATH__|$log_path|g" \
    "$PLIST_TEMPLATE" >"$plist_path"

  plutil -lint "$plist_path" >/dev/null || die "rendered plist is invalid: $plist_path"

  # Re-install: bootout the previous agent first ('not bootstrapped' is fine),
  # then bootstrap the fresh plist.
  launchctl bootout "$GUI_DOMAIN/$label" >/dev/null 2>&1 || true
  launchctl bootstrap "$GUI_DOMAIN" "$plist_path" \
    || die "launchctl bootstrap failed for $label - inspect with: launchctl print $GUI_DOMAIN/$label"

  ok "agent $label installed"
done

# --- Verify (non-fatal: KeepAlive owns the recovery) ----------------------------
info "waiting for the tunnels (up to ~30s)"
api_code="000"
console_code="000"
for _ in $(seq 1 15); do
  api_code="$(curl -s -o /dev/null -m 2 -w '%{http_code}' http://127.0.0.1:8000/openapi.json || true)"
  console_code="$(curl -s -o /dev/null -m 2 -w '%{http_code}' http://127.0.0.1:8080/ || true)"
  if [[ "$api_code" == "200" && "$console_code" == "200" ]]; then
    break
  fi
  sleep 2
done

ok "install complete:"
ok "  Console: http://127.0.0.1:8080/             -> HTTP $console_code"
ok "  API    : http://127.0.0.1:8000/openapi.json -> HTTP $api_code"
ok "  Logs   : $LOG_DIR"
if [[ "$api_code" != "200" || "$console_code" != "200" ]]; then
  warn "some endpoints are not ready - if the cluster is down the agents keep retrying;"
  warn "check 'launchctl print $GUI_DOMAIN/dev.dark-factory.port-forward.api' and the logs."
fi
