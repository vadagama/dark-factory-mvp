#!/usr/bin/env bash
# Remove the port-forward launchd agents installed by install.sh (T-093):
# bootout both agents and delete the rendered plists from ~/Library/LaunchAgents.
# Logs under ~/Library/Logs/dark-factory/port-forward are kept for inspection.
#
# Usage: uninstall.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck source-path=SCRIPTDIR
source "$SCRIPT_DIR/scripts/common.sh"

AGENTS=(
  "dev.dark-factory.port-forward.api"
  "dev.dark-factory.port-forward.console"
)
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
GUI_DOMAIN="gui/$(id -u)"

require_cmds launchctl

for label in "${AGENTS[@]}"; do
  plist_path="$LAUNCH_AGENTS_DIR/$label.plist"
  if launchctl bootout "$GUI_DOMAIN/$label" >/dev/null 2>&1; then
    ok "agent $label stopped"
  else
    info "agent $label was not loaded"
  fi
  if [[ -f "$plist_path" ]]; then
    rm "$plist_path"
    ok "removed $plist_path"
  else
    info "no plist at $plist_path"
  fi
done

ok "uninstall complete - the tunnels are gone. Re-install any time: install.sh"
