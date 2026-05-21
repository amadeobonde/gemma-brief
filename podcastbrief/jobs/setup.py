"""Interactive first-run setup wizard for gemma-brief.

Run:  gemma-brief setup

Three steps:
  1. Checks + installs system dependencies (Ollama, Docker, yt-dlp, ffmpeg)
  2. Collects your Telegram credentials
  3. Adds YouTube playlist URLs (required) and optional RSS feeds

Writes or updates .env in the current working directory.
"""
from __future__ import annotations

import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

import click

# ── ANSI + box-drawing ────────────────────────────────────────────────────────

W = 56  # box width (inner)

def _box_top() -> None:
    click.echo(click.style("  ╔" + "═" * W + "╗", fg="cyan"))

def _box_mid(text: str = "", bold: bool = False) -> None:
    pad = W - len(text)
    styled = click.style(text, bold=bold) if bold else text
    click.echo(click.style("  ║ ", fg="cyan") + styled + " " * max(pad - 1, 0) + click.style(" ║", fg="cyan"))

def _box_div() -> None:
    click.echo(click.style("  ╠" + "═" * W + "╣", fg="cyan"))

def _box_bot() -> None:
    click.echo(click.style("  ╚" + "═" * W + "╝", fg="cyan"))

def _rule(label: str = "") -> None:
    click.echo()
    if label:
        bar = click.style("  ─── ", fg="cyan") + click.style(label, fg="cyan", bold=True) + click.style(" ", fg="cyan")
        width_used = 6 + len(label) + 1
        rest = max(W + 4 - width_used, 0)
        click.echo(bar + click.style("─" * rest, fg="cyan"))
    else:
        click.echo(click.style("  " + "─" * (W + 2), fg="cyan"))

def _ok(msg: str) -> None:
    click.echo(click.style("  ✓  ", fg="green", bold=True) + msg)

def _warn(msg: str) -> None:
    click.echo(click.style("  !  ", fg="yellow", bold=True) + msg)

def _err(msg: str) -> None:
    click.echo(click.style("  ✗  ", fg="red", bold=True) + msg)

def _info(msg: str) -> None:
    click.echo(click.style("  ·  ", fg="bright_black") + msg)

def _step(n: int, total: int, title: str) -> None:
    click.echo()
    label = f"Step {n}/{total}  ·  {title}"
    click.echo(
        click.style("  ┌─ ", fg="cyan", bold=True) +
        click.style(label, bold=True) +
        click.style(" " + "─" * max(W - len(label) - 2, 0), fg="cyan")
    )
    click.echo(click.style("  │", fg="cyan"))

def _step_end() -> None:
    click.echo(click.style("  │", fg="cyan"))


# ── banner ────────────────────────────────────────────────────────────────────

def _banner() -> None:
    click.clear()
    click.echo()
    _box_top()
    _box_mid()
    _box_mid("  gemma-brief  ·  Setup Wizard", bold=True)
    _box_mid("  Local AI briefing engine — Gemma 4 on-device")
    _box_mid()
    _box_div()
    _box_mid()
    _box_mid("  What we'll configure:")
    _box_mid("    1  System dependencies (Ollama · Docker · yt-dlp)")
    _box_mid("    2  Telegram bot  (who receives the briefs)")
    _box_mid("    3  YouTube playlists  (what to watch)")
    _box_mid()
    _box_bot()
    click.echo()
    click.echo(click.style("  Press Ctrl+C at any time to quit.", fg="bright_black"))
    click.echo()


# ── shell helpers ─────────────────────────────────────────────────────────────

def _run(cmd: list[str], *, check: bool = True, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=check, capture_output=True, text=True, timeout=timeout)

def _cmd_exists(name: str) -> bool:
    return shutil.which(name) is not None

def _local_bin() -> Path:
    p = Path.home() / ".local" / "bin"
    p.mkdir(parents=True, exist_ok=True)
    return p


# ── dependency checks ─────────────────────────────────────────────────────────

def _ensure_ollama() -> bool:
    if not _cmd_exists("ollama"):
        if platform.system() == "Darwin":
            _err("Ollama not found.")
            _info("Install the one-click .pkg → https://ollama.com/download")
            _info("Then re-run  gemma-brief setup")
        else:
            _info("Installing Ollama (Linux)…")
            try:
                subprocess.run("curl -fsSL https://ollama.com/install.sh | sh",
                               shell=True, check=True, timeout=300)
                _ok("Ollama installed")
            except Exception as exc:
                _err(f"Ollama install failed: {exc}")
                return False
    else:
        try:
            v = _run(["ollama", "--version"]).stdout.strip().splitlines()[0]
            _ok(f"Ollama  {v}")
        except Exception:
            _ok("Ollama  found")
    return True


def _ensure_gemma(model: str) -> None:
    try:
        if model in _run(["ollama", "list"]).stdout:
            _ok(f"{model}  already pulled")
            return
    except Exception:
        pass
    click.echo()
    click.echo(click.style(f"  Pulling {model} (~9.6 GB, one-time)…", bold=True))
    _info("This takes 5-15 min. You only do this once.")
    click.echo()
    try:
        subprocess.run(["ollama", "pull", model], check=True, timeout=3600)
        _ok(f"{model}  ready")
    except Exception as exc:
        _warn(f"Pull failed: {exc}")
        _info(f"Run  ollama pull {model}  manually, then re-run setup.")


def _ensure_docker() -> bool:
    if not _cmd_exists("docker"):
        if platform.system() == "Darwin":
            _err("Docker not found.")
            _info("Install Docker Desktop → https://www.docker.com/products/docker-desktop")
        else:
            _err("Docker not found.")
            _info("Install Docker Engine → https://docs.docker.com/engine/install/")
        return False
    try:
        v = _run(["docker", "--version"]).stdout.strip()
        _ok(f"Docker  {v}")
    except Exception:
        _ok("Docker  found")
    return True


def _ensure_containers(repo_root: Path) -> None:
    try:
        running = _run(["docker", "ps", "--format", "{{.Names}}"]).stdout
    except Exception:
        _warn("Couldn't check container status — skipping auto-start")
        return
    if "whisper" in running and ("gotenberg" in running):
        _ok("Whisper + Gotenberg  already running")
        return
    compose_file = repo_root / "docker-compose.yml"
    if not compose_file.exists():
        _warn(f"docker-compose.yml not found at {repo_root}")
        return
    _info("Starting Whisper + Gotenberg…")
    try:
        subprocess.run(["docker", "compose", "up", "-d"],
                       cwd=str(repo_root), check=True, timeout=180)
        _ok("Whisper (port 9000) + Gotenberg (port 3000)  running")
    except Exception as exc:
        _warn(f"docker compose up failed: {exc}")
        _info("Run  docker compose up -d  from the repo root manually.")


def _ensure_ytdlp() -> None:
    if _cmd_exists("yt-dlp"):
        try:
            v = _run(["yt-dlp", "--version"]).stdout.strip()
            _ok(f"yt-dlp  {v}")
        except Exception:
            _ok("yt-dlp  found")
        return
    local_bin = _local_bin()
    dest = local_bin / "yt-dlp"
    system, machine = platform.system(), platform.machine()
    if system == "Darwin":
        url = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp_macos"
    elif system == "Linux":
        url = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp"
    else:
        _warn("yt-dlp: unsupported platform — install manually: pip install yt-dlp")
        return
    _info(f"Installing yt-dlp → {dest}")
    try:
        import urllib.request
        urllib.request.urlretrieve(url, str(dest))
        dest.chmod(0o755)
        _ok(f"yt-dlp  {_run(['yt-dlp', '--version']).stdout.strip()}")
    except Exception as exc:
        _warn(f"yt-dlp install failed: {exc}")
        _info("Install manually: pip install yt-dlp")


def _ensure_ffmpeg() -> None:
    if _cmd_exists("ffmpeg"):
        try:
            v = _run(["ffmpeg", "-version"]).stdout.splitlines()[0]
            _ok(f"ffmpeg  {v.split(',')[0]}")
        except Exception:
            _ok("ffmpeg  found")
    else:
        _warn("ffmpeg not found — voice replies will fall back to M4A (still works)")
        _info("Install: brew install ffmpeg  OR  sudo apt install ffmpeg")


# ── .env helpers ──────────────────────────────────────────────────────────────

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
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
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
        new_lines.append("# Added by gemma-brief setup")
        for k, v in remaining.items():
            new_lines.append(f"{k}={v}")
    env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


# ── completion summary ────────────────────────────────────────────────────────

def _show_summary(
    model: str,
    telegram_token: str,
    telegram_chats: str,
    yt_playlists: str,
    rss_feeds: str,
) -> None:
    click.echo()
    _box_top()
    _box_mid()
    _box_mid("  ✓  Setup complete!", bold=True)
    _box_mid()
    _box_div()
    _box_mid()

    def _row(label: str, val: str) -> None:
        label_s = click.style(f"  {label:<14}", fg="bright_black")
        click.echo(click.style("  ║ ", fg="cyan") + label_s + val + click.style(" ║", fg="cyan"))

    n_playlists = len([u for u in yt_playlists.split(",") if u.strip()])
    n_chats = len([c for c in telegram_chats.split(",") if c.strip()])
    bot_handle = "@yourbot" if not telegram_token else "configured"

    _row("Model", click.style(model, bold=True))
    _row("YouTube", click.style(f"{n_playlists} playlist{'s' if n_playlists != 1 else ''}", bold=True))
    if rss_feeds.strip():
        n_rss = len([f for f in rss_feeds.split(",") if f.strip()])
        _row("RSS feeds", click.style(str(n_rss), bold=True))
    _row("Telegram", click.style(f"{n_chats} chat{'s' if n_chats != 1 else ''}", bold=True))
    _box_mid()
    _box_bot()
    click.echo()

    click.echo(click.style("  Start the service (scheduler + Telegram bot):\n", fg="bright_black"))
    click.echo(click.style("    gemma-brief serve\n", bold=True))
    click.echo(click.style("  Or run a one-off brief right now:\n", fg="bright_black"))
    click.echo(click.style("    gemma-brief run-daily\n", bold=True))
    click.echo(click.style("  Install as a macOS background service (survives reboots):\n", fg="bright_black"))
    click.echo(click.style("    ./scripts/install-launchd.sh\n", bold=True))


# ── main wizard ───────────────────────────────────────────────────────────────

def run_setup(*, env_path: Path, repo_root: Path) -> None:
    _banner()
    existing = _read_env(env_path)

    # ── Step 1 / 3  Dependencies ──────────────────────────────────────────────
    _step(1, 3, "System dependencies")

    ollama_ok = _ensure_ollama()
    if ollama_ok:
        _step_end()
        llm_model = click.prompt(
            click.style("  Gemma model", bold=True),
            default=existing.get("LLM_MODEL", "gemma4:e4b"),
        )
        _step_end()
        _ensure_gemma(llm_model)
    else:
        llm_model = existing.get("LLM_MODEL", "gemma4:e4b")

    docker_ok = _ensure_docker()
    if docker_ok:
        _ensure_containers(repo_root)

    _ensure_ytdlp()
    _ensure_ffmpeg()
    _step_end()

    # ── Step 2 / 3  Telegram ─────────────────────────────────────────────────
    _step(2, 3, "Telegram Bot")
    _info("Create a bot at t.me/BotFather → /newbot")
    _info("Get your chat ID by messaging @userinfobot")
    click.echo()

    telegram_token = click.prompt(
        click.style("  Bot token  (TELEGRAM_BOT_TOKEN)", bold=True),
        default=existing.get("TELEGRAM_BOT_TOKEN", ""),
        show_default=False,
        hide_input=True,
        prompt_suffix=click.style("  [hidden] ", fg="bright_black") + ": ",
    )
    telegram_chats = click.prompt(
        click.style("  Chat IDs   (comma-separated)", bold=True),
        default=existing.get("TELEGRAM_CHAT_IDS", ""),
        show_default=False,
        hide_input=True,
        prompt_suffix=click.style("  [hidden] ", fg="bright_black") + ": ",
    )
    _step_end()

    # ── Step 3 / 3  Content sources ───────────────────────────────────────────
    _step(3, 3, "Content Sources")
    _info("Paste one or more YouTube playlist URLs (news, debates, lectures…)")
    _info("New videos uploaded in the last 24 h are picked up automatically.")
    _info("Example: https://www.youtube.com/playlist?list=PLxxxxxxx")
    click.echo()

    yt_playlists = click.prompt(
        click.style("  YouTube playlist URL(s)  (comma-separated)", bold=True),
        default=existing.get("YOUTUBE_PLAYLIST_URLS", ""),
        show_default=bool(existing.get("YOUTUBE_PLAYLIST_URLS")),
    )
    while not yt_playlists.strip():
        _warn("At least one YouTube playlist URL is required.")
        yt_playlists = click.prompt(
            click.style("  YouTube playlist URL(s)", bold=True)
        )

    click.echo()
    _info("RSS podcast feeds — optional, covers any platform with an RSS feed.")
    rss_feeds = click.prompt(
        click.style("  RSS podcast feeds  (comma-separated, blank to skip)", bold=True),
        default=existing.get("RSS_PODCAST_FEEDS", ""),
        show_default=bool(existing.get("RSS_PODCAST_FEEDS")),
    )

    click.echo()
    _info("FRED API key — free macro charts in briefs (fred.stlouisfed.org).")
    fred_key = click.prompt(
        click.style("  FRED API key  (blank to skip)", bold=True),
        default=existing.get("FRED_API_KEY", ""),
        show_default=False,
        hide_input=True,
        prompt_suffix=click.style("  [hidden] ", fg="bright_black") + ": ",
    )
    _step_end()

    # ── write .env ────────────────────────────────────────────────────────────
    values: dict[str, str] = {
        "LLM_MODEL": llm_model,
        "OLLAMA_HOST": existing.get("OLLAMA_HOST", "http://127.0.0.1:11434"),
        "WHISPER_URL": existing.get("WHISPER_URL", "http://localhost:9000"),
        "WHISPER_TIMEOUT_SECONDS": existing.get("WHISPER_TIMEOUT_SECONDS", "1800"),
        "GOTENBERG_URL": existing.get("GOTENBERG_URL", "http://localhost:3000"),
        "NOTES_DIR": existing.get("NOTES_DIR", "./podcast_notes"),
        "PDF_OUT_DIR": existing.get("PDF_OUT_DIR", "./briefs"),
        "LOG_LEVEL": existing.get("LOG_LEVEL", "INFO"),
    }
    if telegram_token:
        values["TELEGRAM_BOT_TOKEN"] = telegram_token
    if telegram_chats:
        values["TELEGRAM_CHAT_IDS"] = telegram_chats
    if yt_playlists:
        values["YOUTUBE_PLAYLIST_URLS"] = yt_playlists
    if rss_feeds:
        values["RSS_PODCAST_FEEDS"] = rss_feeds
    if fred_key:
        values["FRED_API_KEY"] = fred_key

    _write_env(env_path, values)

    _show_summary(llm_model, telegram_token, telegram_chats, yt_playlists, rss_feeds)
