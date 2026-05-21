#!/usr/bin/env bash
# Install gemma-brief as a Linux systemd user service.
# Auto-starts on login, auto-restarts on crash. Logs via journald.
#
#   ./scripts/install-systemd.sh           # install + start
#   ./scripts/install-systemd.sh uninstall # stop + remove

set -euo pipefail

SERVICE="gemma-brief"
INSTALL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
UNIT_DIR="$HOME/.config/systemd/user"
UNIT_FILE="$UNIT_DIR/${SERVICE}.service"

GREEN='\033[1;32m'; CYAN='\033[1;36m'; RED='\033[1;31m'; YELLOW='\033[1;33m'; RESET='\033[0m'
ok()   { printf "  ${GREEN}✓${RESET}  %s\n" "$*"; }
warn() { printf "  ${YELLOW}!${RESET}  %s\n" "$*"; }
err()  { printf "  ${RED}✗${RESET}  %s\n" "$*"; }

uninstall() {
    systemctl --user stop "$SERVICE" 2>/dev/null || true
    systemctl --user disable "$SERVICE" 2>/dev/null || true
    rm -f "$UNIT_FILE"
    systemctl --user daemon-reload 2>/dev/null || true
    ok "Removed ${SERVICE}.service"
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

# Enable systemd lingering so the service survives logout.
loginctl enable-linger "$USER" 2>/dev/null || \
    warn "Could not enable linger (sudo loginctl enable-linger $USER to do it manually)"

mkdir -p "$UNIT_DIR" "$INSTALL_DIR/logs"

cat > "$UNIT_FILE" <<EOF
[Unit]
Description=gemma-brief — local AI briefing engine
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${INSTALL_DIR}
ExecStart=${INSTALL_DIR}/.venv/bin/gemma-brief serve
Restart=on-failure
RestartSec=30
StandardOutput=append:${INSTALL_DIR}/logs/gemma-brief.out.log
StandardError=append:${INSTALL_DIR}/logs/gemma-brief.err.log
Environment="PATH=${INSTALL_DIR}/.venv/bin:/usr/local/bin:/usr/bin:/bin"
Environment="PYTHONUNBUFFERED=1"

[Install]
WantedBy=default.target
EOF

ok "Wrote unit → $UNIT_FILE"

systemctl --user daemon-reload
systemctl --user enable "$SERVICE"
systemctl --user restart "$SERVICE"

printf "\n${CYAN}  gemma-brief is now running as a systemd user service.${RESET}\n\n"
printf "  Useful commands:\n\n"
printf "    systemctl --user status %s\n" "$SERVICE"
printf "    systemctl --user restart %s      # restart\n" "$SERVICE"
printf "    journalctl --user -u %s -f       # live logs\n" "$SERVICE"
printf "    tail -f %s/logs/gemma-brief.err.log\n" "$INSTALL_DIR"
printf "    ./scripts/install-systemd.sh uninstall\n\n"
