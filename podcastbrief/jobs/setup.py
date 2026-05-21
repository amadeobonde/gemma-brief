"""Interactive first-run setup wizard for gemma-brief.

Run:  gemma-brief setup

Three steps:
  1. Checks + installs system dependencies (Ollama, Docker, yt-dlp, ffmpeg)
  2. Collects your Telegram credentials
  3. Adds YouTube playlist URLs (required) and optional RSS feeds

Writes or updates .env in the current working directory.
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
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
    _box_mid("  Local AI briefing engine — Gemma on-device")
    _box_mid()
    _box_div()
    _box_mid()
    _box_mid("  What we'll configure:")
    _box_mid("    1  Model selection  (Gemma 2 · 3 · 4)")
    _box_mid("    2  System dependencies  (Ollama · Docker · yt-dlp)")
    _box_mid("    3  Telegram bot  (who receives the briefs)")
    _box_mid("    4  Sources + schedule  (playlists, feeds, brief time)")
    _box_mid()
    _box_bot()
    click.echo()
    click.echo(click.style("  Press Ctrl+C at any time to quit.", fg="bright_black"))
    click.echo()


# ── Gemma model catalogue ─────────────────────────────────────────────────────

# Each entry: (ollama_tag, display_name, disk_gb, ram_gb, note)
GEMMA_MODELS: list[tuple[str, str, float, int, str]] = [
    # Gemma 4 — multimodal, vision-capable
    ("gemma4:e2b",  "Gemma 4 E2B",  5.0,  7,  "Fastest · fits 8 GB RAM"),
    ("gemma4:e4b",  "Gemma 4 E4B",  9.6,  12, "Best balance of speed + quality"),
    ("gemma4:12b",  "Gemma 4 12B",  13.0, 16, "Higher quality"),
    ("gemma4:27b",  "Gemma 4 27B",  30.0, 35, "Max quality · needs 32 GB+"),
    # Gemma 3 — text-only, strong reasoning
    ("gemma3:4b",   "Gemma 3 4B",   3.3,  6,  "Lightweight · good for 6 GB RAM"),
    ("gemma3:12b",  "Gemma 3 12B",  8.1,  12, "Balanced"),
    ("gemma3:27b",  "Gemma 3 27B",  17.0, 24, "Best text quality · needs 24 GB+"),
    # Gemma 2 — ultra lightweight
    ("gemma2:2b",   "Gemma 2 2B",   1.7,  4,  "Ultra-light · any device"),
    ("gemma2:9b",   "Gemma 2 9B",   5.5,  8,  "Compact"),
    ("gemma2:27b",  "Gemma 2 27B",  16.0, 22, "Needs 24 GB+"),
]

# Context-window presets per model family — bigger ctx = more memory pressure.
MODEL_CTX: dict[str, tuple[int, int]] = {
    "gemma4": (32768, 6144),
    "gemma3": (32768, 6144),
    "gemma2": (8192,  4096),   # Gemma 2 supports 8K natively
}


def _detect_ram_gb() -> int:
    """Best-effort total RAM detection across platforms."""
    system = platform.system()
    try:
        if system == "Darwin":
            raw = subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True).strip()
            return int(raw) // (1024 ** 3)
        elif system == "Linux":
            for line in Path("/proc/meminfo").read_text().splitlines():
                if line.startswith("MemTotal:"):
                    kb = int(line.split()[1])
                    return kb // (1024 ** 2)
        elif system == "Windows":
            raw = subprocess.check_output(
                ["wmic", "computersystem", "get", "TotalPhysicalMemory"], text=True
            )
            for line in raw.splitlines():
                line = line.strip()
                if line.isdigit():
                    return int(line) // (1024 ** 3)
    except Exception:
        pass
    return 0


def _suggest_model(ram_gb: int) -> str:
    """Pick the best default model for the detected RAM."""
    if ram_gb >= 32:
        return "gemma4:27b"
    if ram_gb >= 16:
        return "gemma4:12b"
    if ram_gb >= 12:
        return "gemma4:e4b"
    if ram_gb >= 8:
        return "gemma4:e2b"
    if ram_gb >= 6:
        return "gemma3:4b"
    return "gemma2:2b"


def _pick_model(existing_model: str) -> str:
    """Interactive model selector with RAM-aware recommendation."""
    ram = _detect_ram_gb()
    suggested = _suggest_model(ram) if ram else "gemma4:e4b"
    if existing_model and existing_model != suggested:
        suggested = existing_model  # honour what's already in .env

    # Fixed column widths — keep total line under ~90 chars so nothing wraps.
    #   BAR(5) + marker(2) + num(2) + gap(2) + model(13) + gap(2)
    #   + disk(7) + gap(2) + ram(5) + gap(2) + notes(≤28) = ~70
    C_NUM, C_MODEL, C_DISK, C_RAM = 2, 13, 7, 5
    BAR = click.style("  │  ", fg="cyan")

    if ram:
        click.echo(BAR + click.style(f"Detected RAM: {ram} GB", fg="bright_black"))
    click.echo(BAR)

    # Header
    hdr = f"   {'#':>{C_NUM}}  {'Model':<{C_MODEL}}  {'Disk':>{C_DISK}}  {'RAM':>{C_RAM}}  Notes"
    click.echo(BAR + click.style(hdr, bold=True))
    click.echo(BAR + "   " + "─" * 50)

    default_idx = 1
    prev_family = ""
    family_labels = {
        "gemma4": "Gemma 4  ·  vision + text  ·  128K context",
        "gemma3": "Gemma 3  ·  text-only      ·  128K context",
        "gemma2": "Gemma 2  ·  text-only      ·  8K context",
    }

    for i, (tag, name, disk, rq, note) in enumerate(GEMMA_MODELS, start=1):
        family = tag.split(":")[0]
        if family != prev_family:
            click.echo(BAR)
            click.echo(BAR + "   " + click.style(family_labels.get(family, family), fg="bright_black"))
            prev_family = family

        is_rec = (tag == suggested)
        # Build plain row first (no ANSI), then colorise — avoids padding bugs
        plain = f"  {i:>{C_NUM}}  {name:<{C_MODEL}}  {disk:>5.1f} GB  {rq:>{C_RAM-2}} GB  {note}"
        marker = click.style("▶", fg="cyan", bold=True) if is_rec else " "
        line = BAR + marker + (click.style(plain, bold=True) if is_rec else plain)
        click.echo(line)
        if is_rec:
            default_idx = i

    # Show recommendation as a footer line — never appended to a row (avoids wrap)
    click.echo(BAR)
    if ram:
        rec_name = next((n for t, n, *_ in GEMMA_MODELS if t == suggested), suggested)
        click.echo(
            BAR + click.style("Recommended: ", fg="bright_black") +
            click.style(f"#{default_idx}  {rec_name}  ({suggested})", bold=True)
        )
    click.echo(BAR)

    while True:
        raw = click.prompt(
            click.style("  │  Select number", fg="cyan"),
            default=str(default_idx),
        ).strip()
        if raw.isdigit():
            idx = int(raw)
            if 1 <= idx <= len(GEMMA_MODELS):
                chosen = GEMMA_MODELS[idx - 1][0]
                break
            _warn(f"Pick 1–{len(GEMMA_MODELS)}, or type a tag like gemma4:e4b")
        elif ":" in raw:
            chosen = raw
            break
        else:
            _warn("Enter a number from the list, or a full Ollama tag (e.g. gemma4:e4b)")

    _ok(f"Model → {click.style(chosen, bold=True)}")
    click.echo(click.style("  │", fg="cyan"))
    return chosen


# ── shell helpers ─────────────────────────────────────────────────────────────

def _run(cmd: list[str], *, check: bool = True, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=check, capture_output=True, text=True, timeout=timeout)

def _cmd_exists(name: str) -> bool:
    return shutil.which(name) is not None


def _find_docker() -> str | None:
    """Find the docker binary — checks PATH then known platform-specific locations."""
    found = shutil.which("docker")
    if found:
        return found
    system = platform.system()
    if system == "Darwin":
        candidates = [
            os.path.expanduser("~/.docker/bin/docker"),
            "/Applications/Docker.app/Contents/Resources/bin/docker",
            "/usr/local/bin/docker",
            "/opt/homebrew/bin/docker",
        ]
    elif system == "Linux":
        candidates = [
            "/usr/bin/docker",
            "/usr/local/bin/docker",
            "/snap/bin/docker",
        ]
    elif system == "Windows":
        candidates = [
            r"C:\Program Files\Docker\Docker\resources\bin\docker.exe",
            r"C:\ProgramData\DockerDesktop\version-bin\docker.exe",
        ]
    else:
        candidates = []

    for path in candidates:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            docker_dir = os.path.dirname(path)
            os.environ["PATH"] = docker_dir + os.pathsep + os.environ.get("PATH", "")
            return path
    return None


def _local_bin() -> Path:
    p = Path.home() / ".local" / "bin"
    p.mkdir(parents=True, exist_ok=True)
    return p


# ── dependency checks ─────────────────────────────────────────────────────────

def _ensure_ollama() -> bool:
    if not _cmd_exists("ollama"):
        system = platform.system()
        if system == "Darwin":
            _err("Ollama not found.")
            _info("Install the one-click .pkg → https://ollama.com/download")
            _info("Then re-run  gemma-brief setup")
            return False
        elif system == "Linux":
            _info("Installing Ollama (Linux)…")
            try:
                subprocess.run(
                    "curl -fsSL https://ollama.com/install.sh | sh",
                    shell=True, check=True, timeout=300,
                )
                _ok("Ollama installed")
            except Exception as exc:
                _err(f"Ollama install failed: {exc}")
                return False
        elif system == "Windows":
            _err("Ollama not found.")
            _info("Install from → https://ollama.com/download")
            _info("Then re-run  gemma-brief setup")
            return False
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
    # Estimate download size from catalogue; fall back to "large"
    size_hint = next(
        (f"~{d:.1f} GB" for tag, _, d, _, _ in GEMMA_MODELS if tag == model),
        "large",
    )
    click.echo()
    click.echo(click.style(f"  Pulling {model} ({size_hint}, one-time download)…", bold=True))
    _info("This may take 5–30 min depending on your connection. You only do this once.")
    click.echo()
    try:
        subprocess.run(["ollama", "pull", model], check=True, timeout=7200)
        _ok(f"{model}  ready")
    except Exception as exc:
        _warn(f"Pull failed: {exc}")
        _info(f"Run  ollama pull {model}  manually, then re-run setup.")


def _ensure_docker() -> bool:
    docker_bin = _find_docker()
    if not docker_bin:
        system = platform.system()
        _err("Docker not found.")
        if system == "Darwin":
            _info("Install Docker Desktop → https://www.docker.com/products/docker-desktop")
        elif system == "Linux":
            _info("Install Docker Engine → https://docs.docker.com/engine/install/")
        elif system == "Windows":
            _info("Install Docker Desktop → https://www.docker.com/products/docker-desktop")
            _info("Or enable WSL 2 + Docker from within WSL.")
        return False
    try:
        v = subprocess.run(
            [docker_bin, "--version"], capture_output=True, text=True, timeout=10
        ).stdout.strip()
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
    if "whisper" in running and "gotenberg" in running:
        _ok("Whisper + Gotenberg  already running")
        return
    compose_file = repo_root / "docker-compose.yml"
    if not compose_file.exists():
        _warn(f"docker-compose.yml not found at {repo_root}")
        return
    _info("Starting Whisper + Gotenberg…")
    try:
        subprocess.run(
            ["docker", "compose", "up", "-d"],
            cwd=str(repo_root), check=True, timeout=300,
        )
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
    dest = local_bin / ("yt-dlp.exe" if platform.system() == "Windows" else "yt-dlp")
    system, machine = platform.system(), platform.machine()
    if system == "Darwin":
        url = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp_macos"
    elif system == "Linux":
        url = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp"
    elif system == "Windows":
        url = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
    else:
        _warn("yt-dlp: unsupported platform — install manually: pip install yt-dlp")
        return
    _info(f"Installing yt-dlp → {dest}")
    try:
        import urllib.request
        urllib.request.urlretrieve(url, str(dest))
        if system != "Windows":
            dest.chmod(0o755)
        _ok(f"yt-dlp  installed")
    except Exception as exc:
        _warn(f"yt-dlp auto-install failed: {exc}")
        _info("Install manually:  pip install yt-dlp")


def _ensure_ffmpeg() -> None:
    if _cmd_exists("ffmpeg"):
        try:
            v = _run(["ffmpeg", "-version"]).stdout.splitlines()[0]
            _ok(f"ffmpeg  {v.split(',')[0]}")
        except Exception:
            _ok("ffmpeg  found")
        return
    system = platform.system()
    _warn("ffmpeg not found — voice replies will fall back to M4A (still works)")
    if system == "Darwin":
        _info("Install:  brew install ffmpeg")
    elif system == "Linux":
        _info("Install:  sudo apt install ffmpeg   OR   sudo dnf install ffmpeg")
    elif system == "Windows":
        _info("Install:  winget install ffmpeg   OR  https://ffmpeg.org/download.html")


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
    brief_time: str = "02:00",
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

    _row("Model", click.style(model, bold=True))
    _row("YouTube", click.style(f"{n_playlists} playlist{'s' if n_playlists != 1 else ''}", bold=True))
    if rss_feeds.strip():
        n_rss = len([f for f in rss_feeds.split(",") if f.strip()])
        _row("RSS feeds", click.style(str(n_rss), bold=True))
    _row("Telegram", click.style(f"{n_chats} chat{'s' if n_chats != 1 else ''}", bold=True))
    _row("Daily brief", click.style(f"{brief_time}  (server local time)", bold=True))
    _box_mid()
    _box_bot()
    click.echo()

    system = platform.system()
    click.echo(click.style("  Start the service (scheduler + Telegram bot):\n", fg="bright_black"))
    click.echo(click.style("    gemma-brief serve\n", bold=True))
    click.echo(click.style("  Or generate a brief right now:\n", fg="bright_black"))
    click.echo(click.style("    gemma-brief run\n", bold=True))

    if system == "Darwin":
        click.echo(click.style("  Install as a macOS background service (survives reboots):\n", fg="bright_black"))
        click.echo(click.style("    ./scripts/install-launchd.sh\n", bold=True))
    elif system == "Linux":
        click.echo(click.style("  Install as a Linux background service (survives reboots):\n", fg="bright_black"))
        click.echo(click.style("    ./scripts/install-systemd.sh\n", bold=True))

    click.echo(click.style("  If gemma-brief is not found, use the full path:\n", fg="bright_black"))
    click.echo(click.style("    ./.venv/bin/gemma-brief serve\n", fg="bright_black"))


# ── main wizard ───────────────────────────────────────────────────────────────

def run_setup(*, env_path: Path, repo_root: Path) -> None:
    _banner()
    existing = _read_env(env_path)

    # ── Step 1 / 4  Model ────────────────────────────────────────────────────
    _step(1, 4, "Gemma Model")
    _info("gemma-brief works with the full Gemma suite — pick based on your RAM.")
    llm_model = _pick_model(existing.get("LLM_MODEL", ""))
    _step_end()

    # ── Step 2 / 4  Dependencies ─────────────────────────────────────────────
    _step(2, 4, "System dependencies")
    ollama_ok = _ensure_ollama()
    if ollama_ok:
        _ensure_gemma(llm_model)

    docker_ok = _ensure_docker()
    if docker_ok:
        _ensure_containers(repo_root)

    _ensure_ytdlp()
    _ensure_ffmpeg()
    _step_end()

    # ── Step 3 / 4  Telegram ─────────────────────────────────────────────────
    _step(3, 4, "Telegram Bot")
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

    # ── Step 4 / 4  Content sources ───────────────────────────────────────────
    _step(4, 4, "Content Sources")
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
        yt_playlists = click.prompt(click.style("  YouTube playlist URL(s)", bold=True))

    click.echo()
    _info("RSS podcast feeds — optional, covers any platform with an RSS feed.")
    rss_feeds = click.prompt(
        click.style("  RSS podcast feeds  (comma-separated, blank to skip)", bold=True),
        default=existing.get("RSS_PODCAST_FEEDS", ""),
        show_default=bool(existing.get("RSS_PODCAST_FEEDS")),
    )

    click.echo()
    _info("Daily brief time — when the scheduler runs each day (server local time).")
    _info("Examples: 07:00  morning · 02:00  overnight · 18:00  evening")
    while True:
        brief_time = click.prompt(
            click.style("  Daily brief time  (HH:MM)", bold=True),
            default=existing.get("DAILY_BRIEF_TIME", "02:00"),
        ).strip()
        parts = brief_time.split(":")
        if (
            len(parts) == 2
            and parts[0].isdigit()
            and parts[1].isdigit()
            and 0 <= int(parts[0]) <= 23
            and 0 <= int(parts[1]) <= 59
        ):
            brief_time = f"{int(parts[0]):02d}:{int(parts[1]):02d}"
            _ok(f"Daily brief set for  {click.style(brief_time, bold=True)}")
            break
        _warn(f"{brief_time!r} is not valid — enter HH:MM, e.g. 07:00")

    _step_end()

    # ── write .env ────────────────────────────────────────────────────────────
    # Look up optimal ctx/predict for this model family
    family = llm_model.split(":")[0] if ":" in llm_model else llm_model
    num_ctx, num_predict = MODEL_CTX.get(family, (32768, 6144))

    values: dict[str, str] = {
        "LLM_MODEL": llm_model,
        "LLM_NUM_CTX": str(num_ctx),
        "LLM_NUM_PREDICT": str(num_predict),
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
    values["DAILY_BRIEF_TIME"] = brief_time

    _write_env(env_path, values)
    _show_summary(llm_model, telegram_token, telegram_chats, yt_playlists, rss_feeds, brief_time)
