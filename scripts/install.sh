#!/usr/bin/env bash
# podcastbrief one-command install (idempotent — safe to run twice).
#
# What it does on macOS:
#   1. Ensures uv is installed (used for the Python 3.11 venv).
#   2. Creates ./.venv with Python 3.11 and installs the package + deps.
#   3. Ensures Ollama is installed and pulls gemma4:e4b.
#   4. Ensures Docker is installed and brings up Whisper + Gotenberg containers.
#   5. Installs ffmpeg + yt-dlp standalone binaries to ~/.local/bin (no Homebrew).
#   6. Copies .env.example to .env (only if .env doesn't already exist).
#   7. Prints clear next-step instructions for Spotify OAuth and Telegram setup.
#
# Linux: best-effort. Docker + Ollama install paths assume macOS Homebrew or
#        the official installer scripts; users on other platforms may need to
#        install those two manually before running this.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

LOCAL_BIN="$HOME/.local/bin"
mkdir -p "$LOCAL_BIN"
case ":$PATH:" in
    *":$LOCAL_BIN:"*) ;;
    *) export PATH="$LOCAL_BIN:$PATH" ;;
esac

step() { printf "\n\033[1;36m==> %s\033[0m\n" "$*"; }
ok()   { printf "\033[1;32m✓\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m!\033[0m %s\n" "$*"; }

# 1. uv
if ! command -v uv >/dev/null 2>&1; then
    step "Installing uv (Python toolchain manager)"
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi
ok "uv: $(uv --version 2>&1 | head -1)"

# 2. venv + deps
if [ ! -x "$ROOT/.venv/bin/podcastbrief" ]; then
    step "Creating .venv with Python 3.11 + installing the package"
    uv venv --python 3.11 .venv
    uv pip install -e .
fi
ok "venv ready: $($ROOT/.venv/bin/podcastbrief --help 2>&1 | head -1)"

# 3. Ollama
if ! command -v ollama >/dev/null 2>&1; then
    if [ "$(uname)" = "Darwin" ]; then
        warn "Ollama not found. Install via https://ollama.com/download (one-click .pkg) and re-run."
        exit 1
    else
        step "Installing Ollama (Linux)"
        curl -fsSL https://ollama.com/install.sh | sh
    fi
fi
ok "Ollama: $(ollama --version 2>&1 | head -1)"

if ! ollama list 2>/dev/null | grep -q "^gemma4:e4b"; then
    step "Pulling gemma4:e4b (≈9.6 GB, one-time download)"
    ollama pull gemma4:e4b
fi
ok "gemma4:e4b ready"

# 4. Docker
if ! command -v docker >/dev/null 2>&1; then
    if [ "$(uname)" = "Darwin" ]; then
        warn "Docker not found. Install Docker Desktop from https://www.docker.com/products/docker-desktop and re-run."
        exit 1
    else
        warn "Docker not found. Install Docker Engine for your distro before re-running."
        exit 1
    fi
fi
ok "Docker: $(docker --version 2>&1 | head -1)"

if ! docker ps --format '{{.Names}}' 2>/dev/null | grep -q podcastbrief-whisper; then
    step "Bringing up Whisper + Gotenberg via docker compose"
    docker compose up -d
fi
ok "Whisper + Gotenberg up"

# 5. ffmpeg (Telegram voice replies)
if [ ! -x "$LOCAL_BIN/ffmpeg" ] && ! command -v ffmpeg >/dev/null 2>&1; then
    if [ "$(uname -m)" = "arm64" ] && [ "$(uname)" = "Darwin" ]; then
        step "Installing ffmpeg (macOS arm64 static)"
        curl -fsSL https://www.osxexperts.net/ffmpeg71arm.zip -o /tmp/ffmpeg.zip
        unzip -q -o /tmp/ffmpeg.zip -d /tmp/ffmpeg-extract
        cp /tmp/ffmpeg-extract/ffmpeg "$LOCAL_BIN/ffmpeg"
        chmod +x "$LOCAL_BIN/ffmpeg"
        xattr -d com.apple.quarantine "$LOCAL_BIN/ffmpeg" 2>/dev/null || true
    else
        warn "ffmpeg missing. Install it for your platform — voice replies will fall back to M4A without it."
    fi
fi
command -v ffmpeg >/dev/null 2>&1 && ok "ffmpeg: $(ffmpeg -version 2>&1 | head -1)" || true

# 6. yt-dlp (YouTube ingest)
if [ ! -x "$LOCAL_BIN/yt-dlp" ] && ! command -v yt-dlp >/dev/null 2>&1; then
    step "Installing yt-dlp"
    if [ "$(uname)" = "Darwin" ]; then
        curl -fsSL https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp_macos -o "$LOCAL_BIN/yt-dlp"
    else
        curl -fsSL https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp -o "$LOCAL_BIN/yt-dlp"
    fi
    chmod +x "$LOCAL_BIN/yt-dlp"
fi
command -v yt-dlp >/dev/null 2>&1 && ok "yt-dlp: $(yt-dlp --version 2>&1 | head -1)" || true

# 7. .env
if [ ! -f .env ]; then
    cp .env.example .env
    ok "Created .env from .env.example (fill in your credentials before first run)"
else
    ok ".env already present"
fi

# Final summary + next steps.
cat <<EOM

\033[1;32m✓ Install complete\033[0m

Next steps:

  1. Edit .env and fill in:
       SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, SPOTIFY_PLAYLIST_ID
       TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_IDS
       FRED_API_KEY  (free, https://fred.stlouisfed.org/docs/api/api_key.html)

  2. Spotify OAuth (one-time refresh token):
       Make sure http://127.0.0.1:3000/discovery is in your Spotify app's
       Redirect URIs. Then run:

         docker stop podcastbrief-gotenberg    # free port 3000 briefly
         ./.venv/bin/podcastbrief auth-spotify --write-env
         docker start podcastbrief-gotenberg

  3. Telegram bot:
       Create a bot via @BotFather in Telegram, paste the token into
       TELEGRAM_BOT_TOKEN, and send /start to your bot from each chat in
       TELEGRAM_CHAT_IDS.

  4. Run it:
       ./.venv/bin/podcastbrief run-daily        # one-off daily run
       ./.venv/bin/podcastbrief run-bot          # text + voice + commands
       ./.venv/bin/podcastbrief serve            # scheduler + bot in one process

  5. (macOS) Install as a launchd service so it survives reboots:
       ./scripts/install-launchd.sh

EOM
