#!/usr/bin/env bash
# Install podcastbrief as a macOS LaunchAgent. Survives reboots and process
# crashes (KeepAlive). Logs to ./logs/.
#
#   ./scripts/install-launchd.sh           # install + load
#   ./scripts/install-launchd.sh uninstall # bootout + remove

set -euo pipefail

LABEL="com.podcastbrief.serve"
TEMPLATE="$(cd "$(dirname "$0")" && pwd)/com.podcastbrief.serve.plist"
INSTALL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"

uninstall() {
    if launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
        launchctl bootout "$DOMAIN/$LABEL" || true
        echo "Unloaded $LABEL."
    fi
    rm -f "$TARGET"
    echo "Removed $TARGET."
}

if [[ "${1:-}" == "uninstall" ]]; then
    uninstall
    exit 0
fi

if [[ ! -x "$INSTALL_DIR/.venv/bin/podcastbrief" ]]; then
    echo "ERROR: $INSTALL_DIR/.venv/bin/podcastbrief not found." >&2
    echo "Create the venv and install the package first:" >&2
    echo "  uv venv --python 3.11 .venv && source .venv/bin/activate && pip install -e ." >&2
    exit 1
fi

mkdir -p "$INSTALL_DIR/logs"
mkdir -p "$HOME/Library/LaunchAgents"

# Substitute the install dir into the plist template.
sed "s|__INSTALL_DIR__|$INSTALL_DIR|g" "$TEMPLATE" > "$TARGET"
echo "Installed plist at $TARGET"

# Reload if already loaded so changes take effect.
if launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
    launchctl bootout "$DOMAIN/$LABEL" || true
fi
launchctl bootstrap "$DOMAIN" "$TARGET"
launchctl enable "$DOMAIN/$LABEL"
launchctl kickstart -k "$DOMAIN/$LABEL"

echo
echo "Loaded as $LABEL."
echo
echo "Useful commands:"
echo "  launchctl print $DOMAIN/$LABEL          # status"
echo "  launchctl kickstart -k $DOMAIN/$LABEL    # restart"
echo "  tail -f $INSTALL_DIR/logs/podcastbrief.err.log"
echo "  ./scripts/install-launchd.sh uninstall   # remove"
