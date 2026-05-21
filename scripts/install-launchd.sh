#!/usr/bin/env bash
# Install gemma-brief as a macOS LaunchAgent.
# Auto-starts on login, auto-restarts on crash (KeepAlive). Logs to ./logs/.
#
#   ./scripts/install-launchd.sh           # install + load
#   ./scripts/install-launchd.sh uninstall # bootout + remove

set -euo pipefail

LABEL="com.gemma-brief.serve"
TEMPLATE="$(cd "$(dirname "$0")" && pwd)/com.podcastbrief.serve.plist"
INSTALL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"

GREEN='\033[1;32m'; CYAN='\033[1;36m'; RED='\033[1;31m'; RESET='\033[0m'
ok()  { printf "  ${GREEN}✓${RESET}  %s\n" "$*"; }
err() { printf "  ${RED}✗${RESET}  %s\n" "$*"; }

uninstall() {
    if launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
        launchctl bootout "$DOMAIN/$LABEL" || true
        ok "Unloaded $LABEL"
    fi
    rm -f "$TARGET"
    ok "Removed $TARGET"
}

if [[ "${1:-}" == "uninstall" ]]; then
    uninstall
    exit 0
fi

if [[ ! -x "$INSTALL_DIR/.venv/bin/gemma-brief" ]]; then
    err "$INSTALL_DIR/.venv/bin/gemma-brief not found."
    printf "  Run  ./scripts/install.sh  first.\n"
    exit 1
fi

mkdir -p "$INSTALL_DIR/logs" "$HOME/Library/LaunchAgents"

# Substitute install dir and binary path into the plist template.
sed \
    -e "s|__INSTALL_DIR__|$INSTALL_DIR|g" \
    -e "s|__VENV_BIN__|gemma-brief|g" \
    "$TEMPLATE" > "$TARGET"

ok "Installed plist → $TARGET"

if launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
    launchctl bootout "$DOMAIN/$LABEL" || true
fi
launchctl bootstrap "$DOMAIN" "$TARGET"
launchctl enable "$DOMAIN/$LABEL"
launchctl kickstart -k "$DOMAIN/$LABEL"

printf "\n${CYAN}  gemma-brief is now running as a background service.${RESET}\n\n"
printf "  Useful commands:\n\n"
printf "    launchctl print $DOMAIN/$LABEL | grep -E 'state|pid'\n"
printf "    launchctl kickstart -k $DOMAIN/$LABEL   # restart\n"
printf "    tail -f $INSTALL_DIR/logs/gemma-brief.err.log\n"
printf "    ./scripts/install-launchd.sh uninstall\n\n"
