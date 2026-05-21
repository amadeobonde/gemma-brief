"""Interactive first-run setup wizard for Podcast Brief.

Run:  podcastbrief setup
      (or: .venv/bin/podcastbrief setup  from the repo root)

What it does in order:
  1. Checks + installs system dependencies (Ollama, Docker, yt-dlp, ffmpeg)
  2. Pulls Gemma 4 E4B via Ollama if not already present
  3. Starts Whisper + Gotenberg Docker containers if not running
  4. Collects Telegram credentials and YouTube / RSS content sources
  5. Writes (or updates) .env in the current working directory

After running setup, start the service with:
  podcastbrief serve       # scheduler + Telegram bot in one process
  podcastbrief run-daily   # one-off daily brief run
"""
from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from pathlib import Path

import click


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _ok(msg: str) -> None:
    click.echo(click.style("  ✓ ", fg="green") + msg)


def _warn(msg: str) -> None:
    click.echo(click.style("  ! ", fg="yellow") + msg)


def _err(msg: str) -> None:
    click.echo(click.style("  ✗ ", fg="red") + msg)


def _section(title: str) -> None:
    width = 60
    click.echo()
    click.echo(click.style("─" * width, fg="cyan"))
    click.echo(click.style(f"  {title}", fg="cyan", bold=True))
    click.echo(click.style("─" * width, fg="cyan"))


def _run(cmd: list[str], *, check: bool = True, capture: bool = True, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        check=check,
        capture_output=capture,
        text=True,
        timeout=timeout,
    )


def _cmd_exists(name: str) -> bool:
    return shutil.which(name) is not None


def _local_bin() -> Path:
    p = Path.home() / ".local" / "bin"
    p.mkdir(parents=True, exist_ok=True)
    return p


# ──────────────────────────────────────────────────────────────────────────────
# Dependency checks + installation
# ──────────────────────────────────────────────────────────────────────────────

def _ensure_ollama() -> bool:
    if not _cmd_exists("ollama"):
        if platform.system() == "Darwin":
            _warn(
                "Ollama not found. Install the one-click .pkg from https://ollama.com/download\n"
                "  then re-run  podcastbrief setup"
            )
        else:
            _warn("Installing Ollama (Linux)…")
            try:
                subprocess.run(
                    "curl -fsSL https://ollama.com/install.sh | sh",
                    shell=True, check=True, timeout=300,
                )
                _ok("Ollama installed")
            except Exception as exc:
                _err(f"Ollama install failed: {exc}")
                return False
    else:
        try:
            v = _run(["ollama", "--version"]).stdout.strip().splitlines()[0]
            _ok(f"Ollama: {v}")
        except Exception:
            _ok("Ollama: found")
    return True


def _ensure_gemma(model: str = "gemma4:e4b") -> None:
    try:
        result = _run(["ollama", "list"])
        if model in result.stdout:
            _ok(f"{model}: already pulled")
            return
    except Exception:
        pass
    click.echo(f"  Pulling {model} (~9.6 GB, one-time download)…")
    try:
        subprocess.run(["ollama", "pull", model], check=True, timeout=3600)
        _ok(f"{model} ready")
    except Exception as exc:
        _warn(f"Could not pull {model}: {exc}\n  Run  ollama pull {model}  manually and re-run setup.")


def _ensure_docker() -> bool:
    if not _cmd_exists("docker"):
        if platform.system() == "Darwin":
            _warn(
                "Docker not found. Install Docker Desktop from\n"
                "  https://www.docker.com/products/docker-desktop\n"
                "  then re-run  podcastbrief setup"
            )
        else:
            _warn(
                "Docker not found. Install Docker Engine for your distro\n"
                "  (https://docs.docker.com/engine/install/) then re-run setup."
            )
        return False
    try:
        v = _run(["docker", "--version"]).stdout.strip()
        _ok(f"Docker: {v}")
    except Exception:
        _ok("Docker: found")
    return True


def _ensure_containers(repo_root: Path) -> None:
    try:
        running = _run(["docker", "ps", "--format", "{{.Names}}"]).stdout
    except Exception:
        _warn("Could not check Docker container status.")
        return

    need_start = (
        "podcastbrief-whisper" not in running
        or "podcastbrief-gotenberg" not in running
    )
    if not need_start:
        _ok("Whisper + Gotenberg containers already running")
        return

    compose_file = repo_root / "docker-compose.yml"
    if not compose_file.exists():
        _warn(f"docker-compose.yml not found at {repo_root} — skipping container start")
        return

    click.echo("  Starting Whisper + Gotenberg via docker compose…")
    try:
        subprocess.run(
            ["docker", "compose", "up", "-d"],
            cwd=str(repo_root),
            check=True,
            timeout=180,
        )
        _ok("Whisper + Gotenberg started")
    except Exception as exc:
        _warn(f"docker compose up failed: {exc}\n  Run  docker compose up -d  from {repo_root} manually.")


def _ensure_ytdlp() -> None:
    if _cmd_exists("yt-dlp"):
        try:
            v = _run(["yt-dlp", "--version"]).stdout.strip()
            _ok(f"yt-dlp: {v}")
        except Exception:
            _ok("yt-dlp: found")
        return

    local_bin = _local_bin()
    dest = local_bin / "yt-dlp"
    system = platform.system()

    if system == "Darwin":
        url = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp_macos"
    elif system == "Linux":
        url = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp"
    else:
        _warn("yt-dlp: unsupported platform — install manually: pip install yt-dlp")
        return

    click.echo(f"  Installing yt-dlp to {dest}…")
    try:
        import urllib.request
        urllib.request.urlretrieve(url, str(dest))
        dest.chmod(0o755)
        _ok(f"yt-dlp installed at {dest}")
    except Exception as exc:
        _warn(f"yt-dlp install failed: {exc}\n  Install manually: pip install yt-dlp")


def _ensure_ffmpeg() -> None:
    if _cmd_exists("ffmpeg"):
        try:
            v = _run(["ffmpeg", "-version"]).stdout.splitlines()[0]
            _ok(f"ffmpeg: {v}")
        except Exception:
            _ok("ffmpeg: found")
    else:
        _warn(
            "ffmpeg not found — voice replies will fall back to M4A.\n"
            "  macOS: brew install ffmpeg  OR  see scripts/install.sh for a static binary.\n"
            "  Linux: sudo apt install ffmpeg"
        )


# ──────────────────────────────────────────────────────────────────────────────
# .env helpers
# ──────────────────────────────────────────────────────────────────────────────

def _read_env(env_path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not env_path.exists():
        return out
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip()
    return out


def _write_env(env_path: Path, values: dict[str, str]) -> None:
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()
    else:
        lines = []

    updated: set[str] = set()
    new_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in values:
                new_lines.append(f"{key}={values[key]}")
                updated.add(key)
                continue
        new_lines.append(line)

    remaining = {k: v for k, v in values.items() if k not in updated}
    if remaining:
        new_lines.append("")
        new_lines.append("# Added by podcastbrief setup")
        for k, v in remaining.items():
            new_lines.append(f"{k}={v}")

    env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


# ──────────────────────────────────────────────────────────────────────────────
# Main wizard
# ──────────────────────────────────────────────────────────────────────────────

def run_setup(*, env_path: Path, repo_root: Path) -> None:
    click.clear()
    click.echo(click.style("🎙  Podcast Brief — Setup Wizard", bold=True, fg="cyan"))
    click.echo(click.style("=" * 44, fg="cyan"))
    click.echo(
        "\nThis wizard will:\n"
        "  • Check + install required tools (Ollama, Docker, yt-dlp, ffmpeg)\n"
        "  • Pull Gemma 4 E4B and start Whisper / Gotenberg containers\n"
        "  • Collect your Telegram credentials and YouTube / RSS playlist URLs\n"
        "  • Write your .env\n\n"
        "Press Ctrl+C to quit at any time."
    )

    existing = _read_env(env_path)

    # ── 1. DEPENDENCIES ────────────────────────────────────────────────────────
    _section("1 / 3  Dependencies")

    ollama_ok = _ensure_ollama()
    if ollama_ok:
        llm_model = click.prompt(
            "  Gemma model", default=existing.get("LLM_MODEL", "gemma4:e4b")
        )
        _ensure_gemma(llm_model)
    else:
        llm_model = existing.get("LLM_MODEL", "gemma4:e4b")

    docker_ok = _ensure_docker()
    if docker_ok:
        _ensure_containers(repo_root)

    _ensure_ytdlp()
    _ensure_ffmpeg()

    # ── 2. TELEGRAM ────────────────────────────────────────────────────────────
    _section("2 / 3  Telegram Bot")
    click.echo(
        "  Create a bot via @BotFather → /newbot.\n"
        "  Get your chat ID by messaging @userinfobot.\n"
    )
    telegram_token = click.prompt(
        "  Bot token (TELEGRAM_BOT_TOKEN)",
        default=existing.get("TELEGRAM_BOT_TOKEN", ""),
        show_default=bool(existing.get("TELEGRAM_BOT_TOKEN")),
    )
    telegram_chats = click.prompt(
        "  Chat IDs, comma-separated (TELEGRAM_CHAT_IDS)",
        default=existing.get("TELEGRAM_CHAT_IDS", ""),
        show_default=bool(existing.get("TELEGRAM_CHAT_IDS")),
    )

    # ── 3. CONTENT SOURCES ─────────────────────────────────────────────────────
    _section("3 / 3  Content Sources")

    # YouTube playlists (primary source — at least one required)
    click.echo(
        "  ── YouTube Playlists (required) ──\n"
        "  Paste one or more YouTube playlist URLs, comma-separated.\n"
        "  Example: https://www.youtube.com/playlist?list=PLxxxxxxxxxx\n"
    )
    yt_playlists = click.prompt(
        "  YouTube playlist URL(s) (YOUTUBE_PLAYLIST_URLS)",
        default=existing.get("YOUTUBE_PLAYLIST_URLS", ""),
        show_default=bool(existing.get("YOUTUBE_PLAYLIST_URLS")),
    )
    while not yt_playlists.strip():
        click.echo("  At least one YouTube playlist URL is required.")
        yt_playlists = click.prompt("  YouTube playlist URL(s)")

    # RSS Podcast feeds
    click.echo("\n  ── RSS Podcast Feeds (optional) ──")
    click.echo("  Paste comma-separated RSS feed URLs — one per show.")
    rss_podcast = click.prompt(
        "  RSS podcast feeds (RSS_PODCAST_FEEDS, blank to skip)",
        default=existing.get("RSS_PODCAST_FEEDS", ""),
        show_default=bool(existing.get("RSS_PODCAST_FEEDS")),
    )

    # Optional enrichment
    click.echo("\n  ── Optional: FRED Macro Enrichment ──")
    click.echo("  Free, instant key: https://fred.stlouisfed.org/docs/api/api_key.html")
    fred_key = click.prompt(
        "  FRED API key (blank to skip)",
        default=existing.get("FRED_API_KEY", ""),
        show_default=bool(existing.get("FRED_API_KEY")),
    )

    # ── WRITE .ENV ─────────────────────────────────────────────────────────────
    _section("Writing .env")

    values: dict[str, str] = {}

    if telegram_token:
        values["TELEGRAM_BOT_TOKEN"] = telegram_token
    if telegram_chats:
        values["TELEGRAM_CHAT_IDS"] = telegram_chats

    values["LLM_MODEL"] = llm_model
    values["OLLAMA_HOST"] = existing.get("OLLAMA_HOST", "http://127.0.0.1:11434")
    values["WHISPER_URL"] = existing.get("WHISPER_URL", "http://localhost:9000")
    values["WHISPER_TIMEOUT_SECONDS"] = existing.get("WHISPER_TIMEOUT_SECONDS", "1800")
    values["GOTENBERG_URL"] = existing.get("GOTENBERG_URL", "http://localhost:3000")

    if yt_playlists:
        values["YOUTUBE_PLAYLIST_URLS"] = yt_playlists
    if rss_podcast:
        values["RSS_PODCAST_FEEDS"] = rss_podcast
    if fred_key:
        values["FRED_API_KEY"] = fred_key

    values["NOTES_DIR"] = existing.get("NOTES_DIR", "./podcast_notes")
    values["PDF_OUT_DIR"] = existing.get("PDF_OUT_DIR", "./briefs")
    values["LOG_LEVEL"] = existing.get("LOG_LEVEL", "INFO")

    _write_env(env_path, values)
    _ok(f".env written to {env_path}")

    click.echo()
    click.echo(click.style("✓  Setup complete!", fg="green", bold=True))
    click.echo(
        "\nNext steps:\n\n"
        "  Test the pipeline (one-off daily run):\n"
        "    podcastbrief run-daily\n\n"
        "  Start the scheduler + Telegram bot:\n"
        "    podcastbrief serve\n\n"
        "  (macOS) Install as a launchd service that survives reboots:\n"
        "    ./scripts/install-launchd.sh\n\n"
        "  Re-run setup at any time to update credentials:\n"
        "    podcastbrief setup\n"
    )
