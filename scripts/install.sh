#!/usr/bin/env bash
# gemma-brief — one-command install (idempotent, safe to re-run).
#
# macOS & Linux. Installs:
#   1. uv  (Python toolchain)
#   2. .venv (Python 3.11) + gemma-brief package
#   3. Ollama + pulls gemma4:e4b
#   4. Docker + Whisper / Gotenberg containers
#   5. ffmpeg + yt-dlp binaries → ~/.local/bin
#   6. .env from .env.example (if not present)

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

LOCAL_BIN="$HOME/.local/bin"
mkdir -p "$LOCAL_BIN"
case ":$PATH:" in
    *":$LOCAL_BIN:"*) ;;
    *) export PATH="$LOCAL_BIN:$PATH" ;;
esac

# ── helpers ──────────────────────────────────────────────────────────────────
BOLD='\033[1m'; CYAN='\033[1;36m'; GREEN='\033[1;32m'
YELLOW='\033[1;33m'; RED='\033[1;31m'; RESET='\033[0m'

banner() {
  printf "\n${CYAN}${BOLD}"
  printf "  ╔══════════════════════════════════════════════════╗\n"
  printf "  ║          gemma-brief  ·  Install                 ║\n"
  printf "  ║  Local AI briefing engine — Gemma 4 on-device   ║\n"
  printf "  ╚══════════════════════════════════════════════════╝\n"
  printf "${RESET}\n"
}

step() { printf "\n${CYAN}${BOLD}── %s${RESET}\n" "$*"; }
ok()   { printf "  ${GREEN}✓${RESET}  %s\n" "$*"; }
warn() { printf "  ${YELLOW}!${RESET}  %s\n" "$*"; }
err()  { printf "  ${RED}✗${RESET}  %s\n" "$*"; }

banner

# ── 1. uv ─────────────────────────────────────────────────────────────────────
step "Python toolchain (uv)"
if ! command -v uv >/dev/null 2>&1; then
    printf "  Installing uv...\n"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    source "$HOME/.local/bin/env" 2>/dev/null || export PATH="$HOME/.local/bin:$PATH"
fi
ok "uv $(uv --version 2>&1 | head -1)"

# ── 2. venv + package ─────────────────────────────────────────────────────────
step "Python 3.11 venv + gemma-brief"
if [ ! -x "$ROOT/.venv/bin/gemma-brief" ]; then
    printf "  Creating venv...\n"
    uv venv --python 3.11 .venv
    printf "  Installing package...\n"
    uv pip install -e .
fi
ok "gemma-brief $($ROOT/.venv/bin/gemma-brief --version 2>&1 | head -1)"

# ── 3. Ollama + model ─────────────────────────────────────────────────────────
step "Ollama + Gemma 4 E4B"
if ! command -v ollama >/dev/null 2>&1; then
    if [ "$(uname)" = "Darwin" ]; then
        err "Ollama not found."
        printf "  Install the one-click .pkg → https://ollama.com/download\n"
        printf "  Then re-run this script.\n"
        exit 1
    else
        printf "  Installing Ollama (Linux)...\n"
        curl -fsSL https://ollama.com/install.sh | sh
    fi
fi
ok "Ollama $(ollama --version 2>&1 | head -1)"

if ! ollama list 2>/dev/null | grep -q "^gemma4:e4b"; then
    printf "\n  ${YELLOW}Pulling gemma4:e4b (~9.6 GB, one-time download)...${RESET}\n"
    printf "  This takes 5-15 min on a typical connection. Go make a coffee ☕\n\n"
    ollama pull gemma4:e4b
fi
ok "gemma4:e4b ready"

# ── 4. Docker + containers ────────────────────────────────────────────────────
step "Docker (Whisper + Gotenberg)"
if ! command -v docker >/dev/null 2>&1; then
    err "Docker not found."
    if [ "$(uname)" = "Darwin" ]; then
        printf "  Install Docker Desktop → https://www.docker.com/products/docker-desktop\n"
    else
        printf "  Install Docker Engine → https://docs.docker.com/engine/install/\n"
    fi
    printf "  Then re-run this script.\n"
    exit 1
fi
ok "Docker $(docker --version 2>&1 | head -1)"

if ! docker ps --format '{{.Names}}' 2>/dev/null | grep -q "gemma-brief-whisper\|podcastbrief-whisper"; then
    printf "  Starting Whisper + Gotenberg containers...\n"
    docker compose up -d
fi
ok "Whisper (port 9000) + Gotenberg (port 3000) running"

# ── 5. ffmpeg ─────────────────────────────────────────────────────────────────
step "ffmpeg (voice replies)"
if [ ! -x "$LOCAL_BIN/ffmpeg" ] && ! command -v ffmpeg >/dev/null 2>&1; then
    if [ "$(uname -m)" = "arm64" ] && [ "$(uname)" = "Darwin" ]; then
        printf "  Installing ffmpeg (macOS arm64 static binary)...\n"
        curl -fsSL https://www.osxexperts.net/ffmpeg71arm.zip -o /tmp/ffmpeg.zip
        unzip -q -o /tmp/ffmpeg.zip -d /tmp/ffmpeg-extract
        cp /tmp/ffmpeg-extract/ffmpeg "$LOCAL_BIN/ffmpeg"
        chmod +x "$LOCAL_BIN/ffmpeg"
        xattr -d com.apple.quarantine "$LOCAL_BIN/ffmpeg" 2>/dev/null || true
        ok "ffmpeg installed → $LOCAL_BIN/ffmpeg"
    else
        warn "ffmpeg not found — voice replies will fall back to M4A (still works)"
        printf "  Install: brew install ffmpeg  OR  sudo apt install ffmpeg\n"
    fi
else
    ok "ffmpeg $(ffmpeg -version 2>&1 | head -1 | cut -d' ' -f1-3)"
fi

# ── 6. yt-dlp ─────────────────────────────────────────────────────────────────
step "yt-dlp (YouTube ingest)"
if [ ! -x "$LOCAL_BIN/yt-dlp" ] && ! command -v yt-dlp >/dev/null 2>&1; then
    printf "  Installing yt-dlp...\n"
    if [ "$(uname)" = "Darwin" ]; then
        curl -fsSL https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp_macos \
            -o "$LOCAL_BIN/yt-dlp"
    else
        curl -fsSL https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp \
            -o "$LOCAL_BIN/yt-dlp"
    fi
    chmod +x "$LOCAL_BIN/yt-dlp"
fi
ok "yt-dlp $(yt-dlp --version 2>&1 | head -1)"

# ── 7. .env ───────────────────────────────────────────────────────────────────
step ".env"
if [ ! -f .env ]; then
    cp .env.example .env
    ok "Created .env from .env.example"
else
    ok ".env already present"
fi

# ── done ──────────────────────────────────────────────────────────────────────
printf "\n${GREEN}${BOLD}"
printf "  ╔══════════════════════════════════════════════════╗\n"
printf "  ║            ✓  Install complete!                  ║\n"
printf "  ╚══════════════════════════════════════════════════╝\n"
printf "${RESET}\n"

printf "  Run the setup wizard to add your YouTube playlists\n"
printf "  and Telegram credentials:\n\n"
printf "  ${BOLD}  ./.venv/bin/gemma-brief setup${RESET}\n\n"
printf "  Then start the service:\n\n"
printf "  ${BOLD}  ./.venv/bin/gemma-brief serve${RESET}\n\n"
