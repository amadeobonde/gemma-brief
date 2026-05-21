"""gemma-brief CLI

The CLI is the primary interface. Every command is an abstraction over something
that would otherwise require knowing file paths, service names, API endpoints,
or log locations. The user shouldn't need to know any of that.

Command philosophy (per the project's CLI design principles):
  - Top level: workflow verbs  (setup, serve, run, status, doctor, logs)
  - Subgroups:  resource nouns (source, config)
  - Maintenance: infrequent ops grouped under their plain English names
  - Zero raw internals exposed — no file paths, no env var names in help text
"""
from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import click

from podcastbrief.core.config import load_settings

# ── shared styling ─────────────────────────────────────────────────────────────

def _ok(msg: str)   -> None: click.echo(click.style("  ✓  ", fg="green",  bold=True) + msg)
def _warn(msg: str) -> None: click.echo(click.style("  !  ", fg="yellow", bold=True) + msg)
def _err(msg: str)  -> None: click.echo(click.style("  ✗  ", fg="red",    bold=True) + msg)
def _info(msg: str) -> None: click.echo(click.style("  ·  ", fg="bright_black") + msg)
def _head(msg: str) -> None: click.echo(click.style(f"\n  {msg}", fg="cyan", bold=True))
def _rule() -> None:         click.echo(click.style("  " + "─" * 56, fg="bright_black"))


# ── root group ─────────────────────────────────────────────────────────────────

CONTEXT_SETTINGS = dict(help_option_names=["-h", "--help"], max_content_width=88)


@click.group(context_settings=CONTEXT_SETTINGS)
@click.version_option("1.0.0", prog_name="gemma-brief", message="%(prog)s %(version)s")
def cli() -> None:
    """gemma-brief — local AI briefing engine for YouTube, news & debates.

    \b
    Powered by Gemma (2 / 3 / 4) running fully on-device via Ollama.
    Everything stays on your machine — no cloud, no subscriptions.

    \b
    GETTING STARTED
      gemma-brief setup          first-run wizard (model → Telegram → sources)
      gemma-brief serve          start the scheduler + Telegram bot

    \b
    DAILY USE
      gemma-brief run            pull new videos and generate briefs now
      gemma-brief status         health check: deps · model · vault · service
      gemma-brief doctor         diagnose issues and print fix instructions
      gemma-brief logs           tail the service log

    \b
    SOURCE MANAGEMENT
      gemma-brief source list    show all configured content sources
      gemma-brief source add     add a YouTube playlist or RSS feed
      gemma-brief source remove  remove a source

    \b
    MAINTENANCE  (infrequent)
      gemma-brief cleanup        clear old vault notes
      gemma-brief backfill       re-fetch missing episode audio
      gemma-brief reindex        backfill Whisper word timestamps

    \b
    Run any command with -h for detailed usage:
      gemma-brief run -h
      gemma-brief source -h
    """


# ── setup ──────────────────────────────────────────────────────────────────────

@cli.command("setup", context_settings=CONTEXT_SETTINGS)
@click.option(
    "--env-path",
    type=click.Path(path_type=Path),
    default=Path(".env"),
    metavar="PATH",
    help="Path to write the .env file  [default: ./.env]",
)
def setup_cmd(env_path: Path) -> None:
    """Interactive first-run wizard.

    \b
    Walks you through:
      1  Picking a Gemma model (RAM-aware recommendation)
      2  Installing system dependencies (Ollama, Docker, yt-dlp, ffmpeg)
      3  Connecting your Telegram bot
      4  Adding YouTube playlists (and optional RSS feeds)

    Safe to re-run — updates existing settings without wiping the file.
    """
    from podcastbrief.jobs.setup import run_setup

    repo_root = Path(__file__).resolve().parent.parent
    run_setup(env_path=env_path.resolve(), repo_root=repo_root)


# ── serve ──────────────────────────────────────────────────────────────────────

@cli.command("serve", context_settings=CONTEXT_SETTINGS)
def serve_cmd() -> None:
    """Start the scheduler + Telegram bot in one process.  (recommended)

    \b
    Runs two jobs on a background thread:
      · daily   — 02:00 every day   (pull new videos, generate briefs)
      · cleanup — 03:00 on the 1st  (trim old vault notes)

    The Telegram bot runs in the foreground on the main thread.
    Ctrl+C to stop both.

    \b
    To run as a background service that survives reboots:
      macOS:  ./scripts/install-launchd.sh
      Linux:  ./scripts/install-systemd.sh
    """
    import asyncio
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger

    from podcastbrief.jobs.daily import run_daily
    from podcastbrief.jobs.cleanup import run_cleanup
    from podcastbrief.jobs.bot import run_bot

    s = load_settings()
    logging.basicConfig(level=getattr(logging, s.log_level.upper(), logging.INFO))
    _startup_checks()

    sched = BackgroundScheduler()
    sched.add_job(run_daily,  CronTrigger(hour=2, minute=0), id="daily")
    sched.add_job(run_cleanup, CronTrigger(day=1, hour=3, minute=0), id="cleanup")
    sched.start()
    click.echo(click.style("  Scheduler started. Running Telegram bot in foreground.", fg="green"))
    click.echo(click.style("  Ctrl+C to stop.\n", fg="bright_black"))
    try:
        run_bot()
    finally:
        sched.shutdown()


# ── run ────────────────────────────────────────────────────────────────────────

@cli.command("run", context_settings=CONTEXT_SETTINGS)
@click.option("--hours",    default=24,    metavar="N",  help="Look-back window for new videos  [default: 24].")
@click.option("--dry-run",  is_flag=True,                help="Run the full pipeline but skip Telegram and saving.")
@click.option("--quiet/-q", "quiet", is_flag=True,       help="Suppress INFO logs.")
def run_cmd(hours: int, dry_run: bool, quiet: bool) -> None:
    """Pull new videos and generate briefs right now.

    \b
    Checks every configured YouTube playlist (and RSS feed) for videos
    uploaded in the last --hours hours. Each new video goes through:
      1  yt-dlp download
      2  Whisper transcription
      3  Gemma Pass 1 — extract
      4  Gemma Pass 2 — sharpen
      5  Gemma Pass 3 — ground (Wikipedia + RSS news)
      6  Gotenberg PDF render
      7  Telegram delivery

    \b
    Examples:
      gemma-brief run                   last 24 hours (default)
      gemma-brief run --hours 72        last 3 days
      gemma-brief run --dry-run         end-to-end check, no Telegram
    """
    from podcastbrief.jobs.daily import run_daily

    s = load_settings()
    level = logging.WARNING if quiet else getattr(logging, s.log_level.upper(), logging.INFO)
    logging.basicConfig(level=level)

    click.echo(click.style(f"  Running briefing pipeline ({hours}h look-back)…", fg="cyan"))
    n = run_daily(dry_run=dry_run, hours=hours)
    if n == 0:
        _info(f"No new videos found in the last {hours}h. Try --hours 720 for a 30-day window.")
    else:
        _ok(f"Processed {n} episode{'s' if n != 1 else ''}.")


# ── status ─────────────────────────────────────────────────────────────────────

@cli.command("status", context_settings=CONTEXT_SETTINGS)
def status_cmd() -> None:
    """Show a health dashboard: dependencies, model, vault, service.

    \b
    Everything you'd otherwise check manually — Ollama, Docker, Whisper,
    Gotenberg, yt-dlp, your model, vault stats, and whether the background
    service is running — in one command.
    """
    import httpx

    s = load_settings()

    # ── Dependencies ──────────────────────────────────────────────────────
    _head("Dependencies")
    _rule()

    # Ollama
    try:
        raw = subprocess.check_output(["ollama", "--version"], text=True, timeout=5).strip()
        ver = raw.splitlines()[0] if raw else "found"
        # Check if the configured model is loaded
        loaded = subprocess.check_output(["ollama", "list"], text=True, timeout=5)
        model_pulled = s.llm_model in loaded
        model_note = click.style(f"  model {s.llm_model} pulled", fg="green") \
                     if model_pulled \
                     else click.style(f"  model {s.llm_model} NOT pulled — run: ollama pull {s.llm_model}", fg="yellow")
        _ok(f"Ollama  {ver} {model_note}")
    except FileNotFoundError:
        _err("Ollama  not found — install: https://ollama.com/download")
    except Exception as e:
        _warn(f"Ollama  check failed: {e}")

    # Docker
    docker_bin = shutil.which("docker")
    if docker_bin:
        try:
            dver = subprocess.check_output([docker_bin, "--version"], text=True, timeout=5).strip()
            _ok(f"Docker  {dver}")
        except Exception:
            _ok("Docker  found")
    else:
        _err("Docker  not found — install Docker Desktop or Docker Engine")

    # Whisper (HTTP health check)
    try:
        r = httpx.get(f"{s.whisper_url}/", timeout=3)
        _ok(f"Whisper  {s.whisper_url}  {'reachable' if r.status_code < 500 else 'error ' + str(r.status_code)}")
    except Exception:
        _err(f"Whisper  {s.whisper_url}  not reachable — run: docker compose up -d")

    # Gotenberg (HTTP health check)
    try:
        r = httpx.get(f"{s.gotenberg_url}/health", timeout=3)
        _ok(f"Gotenberg  {s.gotenberg_url}  reachable")
    except Exception:
        _err(f"Gotenberg  {s.gotenberg_url}  not reachable — run: docker compose up -d")

    # yt-dlp
    ytdlp = shutil.which("yt-dlp")
    if ytdlp:
        try:
            v = subprocess.check_output(["yt-dlp", "--version"], text=True, timeout=5).strip()
            _ok(f"yt-dlp  {v}")
        except Exception:
            _ok("yt-dlp  found")
    else:
        _warn("yt-dlp  not found — run: gemma-brief setup")

    # ffmpeg
    ff = shutil.which("ffmpeg") or shutil.which(str(Path.home() / ".local" / "bin" / "ffmpeg"))
    if ff:
        try:
            raw = subprocess.check_output([ff, "-version"], text=True,
                                           stderr=subprocess.STDOUT, timeout=5)
            v = raw.splitlines()[0].split(",")[0].strip()
            _ok(f"ffmpeg  {v}")
        except Exception:
            _ok("ffmpeg  found")
    else:
        _warn("ffmpeg  not found — voice replies fall back to M4A")

    # ── Configuration ─────────────────────────────────────────────────────
    _head("Configuration")
    _rule()

    family = s.llm_model.split(":")[0]
    vision = family in {"gemma3", "gemma4"}
    _info(f"Model      {click.style(s.llm_model, bold=True)}  "
          f"ctx={s.llm_num_ctx}  predict={s.llm_num_predict}  "
          f"{'vision ✓' if vision else 'text-only'}")

    n_yt  = len(s.youtube_playlist_url_list)
    n_rss = len(s.rss_podcast_feed_list)
    _info(f"Sources    {n_yt} YouTube playlist{'s' if n_yt != 1 else ''}"
          + (f"  ·  {n_rss} RSS feed{'s' if n_rss != 1 else ''}" if n_rss else ""))

    n_chats = len(s.chat_id_list)
    tg_status = click.style("configured", fg="green") if s.telegram_bot_token else click.style("NOT SET — run: gemma-brief setup", fg="yellow")
    _info(f"Telegram   {tg_status}  ({n_chats} chat{'s' if n_chats != 1 else ''})")

    # ── Vault ─────────────────────────────────────────────────────────────
    _head("Vault")
    _rule()

    notes_dir = Path(s.notes_dir)
    if notes_dir.exists():
        md_files = list(notes_dir.glob("*.md"))
        md_files = [f for f in md_files if f.name != "INDEX.md"]
        if md_files:
            newest = max(md_files, key=lambda f: f.stat().st_mtime)
            newest_date = datetime.fromtimestamp(newest.stat().st_mtime).strftime("%Y-%m-%d")
            # Try to read the title from frontmatter
            newest_title = newest.stem
            try:
                for line in newest.read_text(encoding="utf-8", errors="ignore").splitlines()[:5]:
                    if line.startswith("# "):
                        newest_title = line[2:].strip()[:60]
                        break
            except Exception:
                pass
            _ok(f"{len(md_files)} brief{'s' if len(md_files) != 1 else ''}  ·  "
                f"last: {newest_date}  \"{newest_title}\"")
        else:
            _info("No briefs yet — run: gemma-brief run")
    else:
        _info("Vault not initialized — run: gemma-brief run")

    audio_dir = Path(s.audio_store_path)
    if audio_dir.exists():
        audio_files = list(audio_dir.glob("*"))
        total_bytes = sum(f.stat().st_size for f in audio_files if f.is_file())
        _info(f"Audio store  {len(audio_files)} file{'s' if len(audio_files) != 1 else ''}  ·  "
              f"{total_bytes / (1024**3):.1f} GB")

    # ── Background service ─────────────────────────────────────────────────
    _head("Background Service")
    _rule()

    svc_status = _service_status()
    if svc_status == "running":
        _ok(f"Service  running")
        _info("  Stop:     see platform instructions in gemma-brief serve -h")
    elif svc_status == "stopped":
        _warn("Service  stopped — start: gemma-brief serve")
        _info("  Or install as background service: ./scripts/install-launchd.sh (macOS)")
        _info("                                    ./scripts/install-systemd.sh  (Linux)")
    else:
        _info("Service  not installed as background service")
        _info("  Run in foreground:  gemma-brief serve")

    click.echo()


# ── doctor ─────────────────────────────────────────────────────────────────────

@cli.command("doctor", context_settings=CONTEXT_SETTINGS)
def doctor_cmd() -> None:
    """Diagnose configuration and dependency issues with fix instructions.

    \b
    Checks everything the pipeline depends on and prints an actionable
    fix for each problem found. Clean output means you're good to go.
    """
    import httpx

    s = load_settings()
    issues: list[str] = []

    click.echo(click.style("\n  Running diagnostics…\n", fg="cyan"))

    # Ollama
    if not shutil.which("ollama"):
        issues.append("Ollama not installed.")
        _err("Ollama not found")
        fix = "  → macOS: download .pkg from https://ollama.com/download\n"
        fix += "  → Linux: curl -fsSL https://ollama.com/install.sh | sh"
        click.echo(click.style(fix, fg="yellow"))
    else:
        try:
            loaded = subprocess.check_output(["ollama", "list"], text=True, timeout=5)
            if s.llm_model not in loaded:
                issues.append(f"Model {s.llm_model} not pulled.")
                _err(f"Model  {s.llm_model}  not pulled")
                click.echo(click.style(f"  → Run: ollama pull {s.llm_model}", fg="yellow"))
            else:
                _ok(f"Ollama + {s.llm_model}")
        except Exception as e:
            issues.append(f"Ollama check failed: {e}")
            _err(f"Ollama check failed: {e}")
            click.echo(click.style("  → Make sure Ollama is running (open the app, or: ollama serve)", fg="yellow"))

    # Docker
    docker = shutil.which("docker")
    if not docker:
        issues.append("Docker not found.")
        _err("Docker not found")
        click.echo(click.style("  → Install Docker Desktop: https://www.docker.com/products/docker-desktop", fg="yellow"))
    else:
        try:
            subprocess.check_output([docker, "ps"], timeout=10, stderr=subprocess.DEVNULL)
            _ok("Docker running")
        except subprocess.CalledProcessError:
            issues.append("Docker daemon not running.")
            _err("Docker daemon not running")
            click.echo(click.style("  → Open Docker Desktop, or: sudo systemctl start docker", fg="yellow"))

    # Whisper
    try:
        httpx.get(f"{s.whisper_url}/", timeout=3)
        _ok(f"Whisper  {s.whisper_url}")
    except Exception:
        issues.append(f"Whisper not reachable at {s.whisper_url}")
        _err(f"Whisper not reachable  ({s.whisper_url})")
        click.echo(click.style("  → Run: docker compose up -d", fg="yellow"))
        click.echo(click.style(f"  → Check .env: WHISPER_URL={s.whisper_url}", fg="yellow"))

    # Gotenberg
    try:
        httpx.get(f"{s.gotenberg_url}/health", timeout=3)
        _ok(f"Gotenberg  {s.gotenberg_url}")
    except Exception:
        issues.append(f"Gotenberg not reachable at {s.gotenberg_url}")
        _err(f"Gotenberg not reachable  ({s.gotenberg_url})")
        click.echo(click.style("  → Run: docker compose up -d", fg="yellow"))

    # Telegram token
    if not s.telegram_bot_token:
        issues.append("Telegram bot token not set.")
        _err("TELEGRAM_BOT_TOKEN not set")
        click.echo(click.style("  → Run: gemma-brief setup  (step 3)", fg="yellow"))
    else:
        try:
            r = httpx.get(
                f"https://api.telegram.org/bot{s.telegram_bot_token}/getMe",
                timeout=10,
            )
            if r.status_code == 200:
                name = r.json().get("result", {}).get("username", "unknown")
                _ok(f"Telegram  @{name}")
            else:
                issues.append("Telegram token invalid.")
                _err(f"Telegram token invalid  (HTTP {r.status_code})")
                click.echo(click.style("  → Create a new bot at t.me/BotFather → /newbot, then run: gemma-brief setup", fg="yellow"))
        except Exception:
            _warn("Telegram  token set but could not verify (network issue?)")

    # Chat IDs
    if not s.telegram_chat_ids:
        issues.append("No Telegram chat IDs configured.")
        _err("TELEGRAM_CHAT_IDS not set")
        click.echo(click.style("  → Run: gemma-brief setup  (step 3)", fg="yellow"))
    else:
        _ok(f"Telegram  {len(s.chat_id_list)} chat{'s' if len(s.chat_id_list) != 1 else ''} configured")

    # YouTube sources
    if not s.youtube_playlist_url_list:
        issues.append("No YouTube playlists configured.")
        _err("No YouTube playlists configured")
        click.echo(click.style("  → Run: gemma-brief source add --playlist <url>", fg="yellow"))
    else:
        _ok(f"Sources  {len(s.youtube_playlist_url_list)} YouTube playlist{'s' if len(s.youtube_playlist_url_list) != 1 else ''}")

    # yt-dlp
    if not shutil.which("yt-dlp"):
        issues.append("yt-dlp not found.")
        _err("yt-dlp not found")
        click.echo(click.style("  → Run: gemma-brief setup  (re-runs the dependency installer)", fg="yellow"))
    else:
        _ok("yt-dlp found")

    # Summary
    click.echo()
    if not issues:
        click.echo(click.style("  ✓  All checks passed — gemma-brief is ready.\n", fg="green", bold=True))
    else:
        click.echo(click.style(
            f"  {len(issues)} issue{'s' if len(issues) != 1 else ''} found. "
            "Fix the items above, then run  gemma-brief doctor  again.\n",
            fg="yellow", bold=True,
        ))


# ── logs ───────────────────────────────────────────────────────────────────────

@cli.command("logs", context_settings=CONTEXT_SETTINGS)
@click.option("--follow", "-f", is_flag=True, help="Stream new log lines as they arrive.")
@click.option("--lines",  "-n", default=50, metavar="N", help="Number of lines to show  [default: 50].")
@click.option("--errors", is_flag=True, help="Show only ERROR and WARNING lines.")
def logs_cmd(follow: bool, lines: int, errors: bool) -> None:
    """Tail the gemma-brief service log.

    \b
    Finds the log file regardless of where the service is installed.
    Works whether you're running via launchd, systemd, or gemma-brief serve.

    \b
    Examples:
      gemma-brief logs                 last 50 lines
      gemma-brief logs -n 100          last 100 lines
      gemma-brief logs --follow        stream live  (Ctrl+C to stop)
      gemma-brief logs --errors        errors and warnings only
    """
    repo_root = Path(__file__).resolve().parent.parent
    log_file = repo_root / "logs" / "gemma-brief.err.log"

    if not log_file.exists():
        # Try the out log too
        out_log = repo_root / "logs" / "gemma-brief.out.log"
        if out_log.exists():
            log_file = out_log
        else:
            _warn("No log file found yet.")
            _info("The log file is created when gemma-brief serve runs as a background service.")
            _info(f"Expected location: {log_file}")
            _info("To see live output, run:  gemma-brief serve  in a terminal.")
            return

    if errors:
        # Filter-only mode using grep
        filter_args = ["-E", "ERROR|WARNING|CRITICAL"]
        cmd = ["grep"] + filter_args + [str(log_file)]
        if follow:
            cmd = ["tail", f"-n{lines}", "-f", str(log_file)]
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True)
            try:
                assert proc.stdout
                for line in proc.stdout:
                    if any(lvl in line for lvl in ("ERROR", "WARNING", "CRITICAL")):
                        level_color = "red" if "ERROR" in line or "CRITICAL" in line else "yellow"
                        click.echo(click.style(line.rstrip(), fg=level_color))
            except KeyboardInterrupt:
                pass
            finally:
                proc.terminate()
            return
        try:
            out = subprocess.check_output(
                ["grep", "-E", "ERROR|WARNING|CRITICAL", str(log_file)], text=True
            )
            tail_lines = out.splitlines()[-lines:]
            for line in tail_lines:
                level_color = "red" if "ERROR" in line or "CRITICAL" in line else "yellow"
                click.echo(click.style(line, fg=level_color))
        except subprocess.CalledProcessError:
            _info("No errors or warnings in the log.")
        return

    tail_cmd = ["tail", f"-n{lines}"]
    if follow:
        tail_cmd.append("-f")
    tail_cmd.append(str(log_file))

    click.echo(click.style(f"  {log_file}\n", fg="bright_black"))
    try:
        subprocess.run(tail_cmd)
    except KeyboardInterrupt:
        pass


# ── source subgroup ────────────────────────────────────────────────────────────

@cli.group("source", context_settings=CONTEXT_SETTINGS, invoke_without_command=True)
@click.pass_context
def source_group(ctx: click.Context) -> None:
    """Manage content sources (YouTube playlists and RSS feeds).

    \b
    Commands:
      gemma-brief source list            show all configured sources
      gemma-brief source add --playlist  add a YouTube playlist
      gemma-brief source add --rss       add a podcast RSS feed
      gemma-brief source remove          remove a source by URL
    """
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@source_group.command("list", context_settings=CONTEXT_SETTINGS)
def source_list_cmd() -> None:
    """List all configured content sources."""
    s = load_settings()
    yt = s.youtube_playlist_url_list
    rss = s.rss_podcast_feed_list

    if not yt and not rss:
        _warn("No sources configured.")
        _info("Add one:  gemma-brief source add --playlist <youtube-url>")
        return

    if yt:
        _head(f"YouTube Playlists  ({len(yt)})")
        _rule()
        for url in yt:
            _info(url)

    if rss:
        _head(f"RSS Feeds  ({len(rss)})")
        _rule()
        for url in rss:
            _info(url)

    click.echo()


@source_group.command("add", context_settings=CONTEXT_SETTINGS)
@click.option("--playlist", "playlist_url", default="", metavar="URL",
              help="YouTube playlist URL to add.")
@click.option("--rss",      "rss_url",      default="", metavar="URL",
              help="Podcast RSS feed URL to add.")
@click.option("--env-path", type=click.Path(path_type=Path), default=Path(".env"), hidden=True)
@click.argument("url", required=False, default="")
def source_add_cmd(playlist_url: str, rss_url: str, env_path: Path, url: str) -> None:
    """Add a content source.

    \b
    Accepts a YouTube playlist URL or a podcast RSS feed URL.
    Deduplicates — adding the same URL twice is a no-op.

    \b
    Examples:
      gemma-brief source add --playlist https://youtube.com/playlist?list=PL...
      gemma-brief source add --rss https://feeds.simplecast.com/your-show
      gemma-brief source add https://youtube.com/playlist?list=PL...   (auto-detected)
    """
    from podcastbrief.jobs.add_source import register_source

    # Auto-detect if a bare URL was passed as a positional argument
    if url and not playlist_url and not rss_url:
        if "youtube.com" in url or "youtu.be" in url:
            playlist_url = url
        else:
            rss_url = url

    if not playlist_url and not rss_url:
        raise click.UsageError(
            "Provide a URL:  --playlist <youtube-url>  or  --rss <feed-url>"
        )

    register_source(
        env_path=env_path.resolve(),
        playlist_url=playlist_url,
        rss_url=rss_url,
    )

    if playlist_url:
        _ok(f"Added playlist:  {playlist_url}")
    if rss_url:
        _ok(f"Added RSS feed:  {rss_url}")
    _info("Changes take effect on the next  gemma-brief run  or  gemma-brief serve  restart.")


@source_group.command("remove", context_settings=CONTEXT_SETTINGS)
@click.argument("url")
@click.option("--env-path", type=click.Path(path_type=Path), default=Path(".env"), hidden=True)
def source_remove_cmd(url: str, env_path: Path) -> None:
    """Remove a content source by URL.

    \b
    Example:
      gemma-brief source remove https://youtube.com/playlist?list=PL...
    """
    from podcastbrief.jobs.add_source import _read_env, _write_env

    env_path = env_path.resolve()
    env = _read_env(env_path)
    changed = False

    for key in ("YOUTUBE_PLAYLIST_URLS", "RSS_PODCAST_FEEDS"):
        existing = [u.strip() for u in env.get(key, "").split(",") if u.strip()]
        if url in existing:
            existing.remove(url)
            env[key] = ",".join(existing)
            changed = True

    if not changed:
        _warn(f"URL not found in any source list: {url}")
        _info("Run  gemma-brief source list  to see configured sources.")
        raise SystemExit(1)

    _write_env(env_path, {k: env[k] for k in ("YOUTUBE_PLAYLIST_URLS", "RSS_PODCAST_FEEDS") if k in env})
    _ok(f"Removed:  {url}")
    _info("Changes take effect on the next  gemma-brief run  or restart.")


# ── maintenance commands ────────────────────────────────────────────────────────

@cli.command("cleanup", context_settings=CONTEXT_SETTINGS)
@click.option("--dry-run", is_flag=True, help="Show what would be removed without deleting.")
def cleanup_cmd(dry_run: bool) -> None:
    """Clear old vault notes to free disk space.

    Removes notes older than the configured retention window (default: 90 days).
    Run monthly, or set up the background service which does it automatically.
    """
    from podcastbrief.jobs.cleanup import run_cleanup

    n = run_cleanup()
    if dry_run:
        _info(f"Would remove {n} note{'s' if n != 1 else ''}.")
    else:
        _ok(f"Removed {n} note{'s' if n != 1 else ''}.")


@cli.command("backfill", context_settings=CONTEXT_SETTINGS)
def backfill_cmd() -> None:
    """Re-fetch missing episode audio files.

    The audio store is required for the /debate command (clip stitching).
    Run this if you set up the audio store after initial processing, or if
    files were deleted.
    """
    from podcastbrief.jobs.maintenance import redownload_audio, format_results

    s = load_settings()
    logging.basicConfig(level=getattr(logging, s.log_level.upper(), logging.INFO))
    results = redownload_audio(s)
    click.echo(format_results(results))


@cli.command("reindex", context_settings=CONTEXT_SETTINGS)
def reindex_cmd() -> None:
    """Backfill Whisper word timestamps for existing vault notes.

    Required for /debate audio clip generation. Idempotent — notes that
    already have word-level sidecars are skipped. Run backfill first if
    audio files are missing.
    """
    from podcastbrief.jobs.maintenance import reindex_timestamps, format_results

    s = load_settings()
    logging.basicConfig(level=getattr(logging, s.log_level.upper(), logging.INFO))
    results = reindex_timestamps(s)
    click.echo(format_results(results))


# ── test-brief (power user / dev) ──────────────────────────────────────────────

@cli.command("test-brief", context_settings=CONTEXT_SETTINGS, hidden=False)
@click.argument("audio_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--show",    required=True, help="Show name.")
@click.option("--title",   required=True, help="Episode title.")
@click.option("--artwork", type=click.Path(exists=True, dir_okay=False, path_type=Path), default=None)
@click.option("--out",     type=click.Path(path_type=Path), default=Path("./test_brief.pdf"),
              help="Output PDF path  [default: ./test_brief.pdf]")
def test_brief_cmd(audio_path: Path, show: str, title: str, artwork: Path | None, out: Path) -> None:
    """Run the full pipeline against a local audio file and write a PDF.

    \b
    Useful for testing a new model or troubleshooting without downloading
    from YouTube. Skips Telegram delivery — writes PDF to --out.

    \b
    Example:
      gemma-brief test-brief episode.mp3 --show "My Show" --title "Episode 42"
    """
    from datetime import datetime, timezone
    from podcastbrief.adapters.whisper_http import WhisperHttpTranscriber
    from podcastbrief.adapters.ollama_gemma import OllamaGemma
    from podcastbrief.adapters.gotenberg_renderer import GotenbergRenderer
    from podcastbrief.briefing.extractor import extract_structure
    from podcastbrief.briefing.interrogator import interrogate
    from podcastbrief.briefing.schemas import RenderInput

    s = load_settings()
    logging.basicConfig(level=getattr(logging, s.log_level.upper(), logging.INFO))

    click.echo(click.style(f"  Transcribing {audio_path.name}…", fg="cyan"))
    audio_bytes = audio_path.read_bytes()
    art_bytes   = artwork.read_bytes() if artwork else None

    whisper  = WhisperHttpTranscriber(base_url=s.whisper_url, timeout_seconds=s.whisper_timeout_seconds)
    llm      = OllamaGemma(host=s.ollama_host, model=s.llm_model, num_ctx=s.llm_num_ctx, num_predict=s.llm_num_predict)
    renderer = GotenbergRenderer(base_url=s.gotenberg_url)

    transcript = whisper.transcribe(audio_bytes, filename=audio_path.name)
    click.echo(click.style("  Running Gemma passes…", fg="cyan"))
    structure = extract_structure(llm=llm, transcript=transcript, show_name=show,
                                  episode_title=title, artwork_png=art_bytes)
    brief = interrogate(llm=llm, structure=structure, transcript=transcript)

    render_in = RenderInput(
        brief=brief,
        show_name=show,
        episode_title=title,
        runtime="",
        pub_date=None,
        source_url=None,
        artwork_png=art_bytes,
        suggestions=[],
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    )
    click.echo(click.style("  Rendering PDF…", fg="cyan"))
    pdf = renderer.render(render_in)
    out.write_bytes(pdf)
    _ok(f"Wrote {out}  ({len(pdf) // 1024} KB)")


# ── completions ────────────────────────────────────────────────────────────────

@cli.command("completions", context_settings=CONTEXT_SETTINGS)
@click.argument("shell", type=click.Choice(["bash", "zsh", "fish"]))
def completions_cmd(shell: str) -> None:
    """Print shell completion script.

    \b
    Add to your shell profile to enable tab-completion for all commands:

    \b
      bash:
        gemma-brief completions bash >> ~/.bash_completion
        echo 'source ~/.bash_completion' >> ~/.bashrc

    \b
      zsh:
        gemma-brief completions zsh >> ~/.zfunc/_gemma-brief
        echo 'fpath=(~/.zfunc $fpath); autoload -Uz compinit && compinit' >> ~/.zshrc

    \b
      fish:
        gemma-brief completions fish > ~/.config/fish/completions/gemma-brief.fish
    """
    prog_name = "gemma-brief"
    env_var = "_GEMMA_BRIEF_COMPLETE"

    if shell == "bash":
        script = f"""_{prog_name.upper().replace('-','_')}_COMPLETE=bash_source {prog_name}
# Or: eval "$({prog_name} completions bash)" """
        # Use Click's built-in completion
        os.environ[env_var] = "bash_source"
    elif shell == "zsh":
        os.environ[env_var] = "zsh_source"
    elif shell == "fish":
        os.environ[env_var] = "fish_source"

    # Invoke Click's completion mechanism
    try:
        from click.shell_completion import ShellComplete
        from click._compat import _default_text_stdout
        comp = ShellComplete(cli, {}, prog_name, env_var)
        comp.source()
    except Exception:
        # Fallback: instruct user to use Click's built-in mechanism
        click.echo(f"# Add to your {shell} profile:")
        if shell == "bash":
            click.echo(f'eval "$(_GEMMA_BRIEF_COMPLETE=bash_source {prog_name})"')
        elif shell == "zsh":
            click.echo(f'eval "$(_GEMMA_BRIEF_COMPLETE=zsh_source {prog_name})"')
        elif shell == "fish":
            click.echo(f"_GEMMA_BRIEF_COMPLETE=fish_source {prog_name} | source")


# ── backward-compat aliases (hidden) ──────────────────────────────────────────

@cli.command("run-daily", hidden=True)
@click.option("--dry-run", is_flag=True)
@click.option("--hours", default=24, type=int)
def run_daily_compat(dry_run: bool, hours: int) -> None:
    """Alias for `run` (kept for backward compatibility)."""
    from podcastbrief.jobs.daily import run_daily
    n = run_daily(dry_run=dry_run, hours=hours)
    click.echo(f"Processed {n} episode(s).")


@cli.command("run-bot", hidden=True)
def run_bot_compat() -> None:
    """Alias for the bot-only mode (use `serve` instead)."""
    from podcastbrief.jobs.bot import run_bot
    s = load_settings()
    logging.basicConfig(level=getattr(logging, s.log_level.upper(), logging.INFO))
    _startup_checks()
    run_bot()


@cli.command("cleanup-notes", hidden=True)
def cleanup_notes_compat() -> None:
    """Alias for `cleanup` (kept for backward compatibility)."""
    from podcastbrief.jobs.cleanup import run_cleanup
    n = run_cleanup()
    click.echo(f"Cleared {n} note(s).")


@cli.command("redownload-audio", hidden=True)
def redownload_audio_compat() -> None:
    """Alias for `backfill` (kept for backward compatibility)."""
    from podcastbrief.jobs.maintenance import redownload_audio, format_results
    s = load_settings()
    logging.basicConfig(level=getattr(logging, s.log_level.upper(), logging.INFO))
    click.echo(format_results(redownload_audio(s)))


@cli.command("reindex-timestamps", hidden=True)
def reindex_timestamps_compat() -> None:
    """Alias for `reindex` (kept for backward compatibility)."""
    from podcastbrief.jobs.maintenance import reindex_timestamps, format_results
    s = load_settings()
    logging.basicConfig(level=getattr(logging, s.log_level.upper(), logging.INFO))
    click.echo(format_results(reindex_timestamps(s)))


@cli.command("add", hidden=True)
@click.option("--playlist", "playlist_url", default="")
@click.option("--rss", "rss_url", default="")
@click.option("--env-path", type=click.Path(path_type=Path), default=Path(".env"))
def add_compat(playlist_url: str, rss_url: str, env_path: Path) -> None:
    """Alias for `source add` (kept for backward compatibility)."""
    from podcastbrief.jobs.add_source import register_source
    register_source(env_path=env_path.resolve(), playlist_url=playlist_url, rss_url=rss_url)


# ── internal helpers ──────────────────────────────────────────────────────────

def _startup_checks() -> None:
    """Warn about missing optional deps needed by /debate."""
    try:
        import podcastbrief.adapters.pydub_clip_extractor  # noqa: F401
    except ImportError:
        click.echo(
            "⚠️  pydub not installed — /debate audio stitching disabled. "
            "Install: pip install 'pydub>=0.25.1'",
            err=True,
        )
        return
    missing = [b for b in ("ffmpeg", "ffprobe") if not shutil.which(b)]
    if missing:
        click.echo(
            f"⚠️  {', '.join(missing)} missing from PATH — /debate will fail. "
            "Install ffmpeg or run: gemma-brief setup",
            err=True,
        )


def _service_status() -> str:
    """Return 'running', 'stopped', or 'not_installed'."""
    system = platform.system()
    try:
        if system == "Darwin":
            r = subprocess.run(
                ["launchctl", "print", f"gui/{os.getuid()}/com.gemma-brief.serve"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0:
                return "running" if "running" in r.stdout else "stopped"
        elif system == "Linux":
            r = subprocess.run(
                ["systemctl", "--user", "is-active", "gemma-brief"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0:
                return "running"
            if r.stdout.strip() in ("inactive", "failed", "dead"):
                return "stopped"
    except Exception:
        pass
    return "not_installed"


if __name__ == "__main__":
    cli()
