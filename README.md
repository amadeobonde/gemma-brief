# gemma-brief

**Local AI briefing engine for YouTube, news & debates.**  
Point it at any YouTube playlist — news channels, debate shows, lectures, podcasts. It transcribes every new video with Whisper, runs three passes of Gemma analysis, and sends you a structured PDF brief via Telegram. Then lets you ask questions, run debates, and quiz yourself across everything in your library.

Everything runs **fully on-device**. No OpenAI. No subscriptions. No data leaving your machine.  
Works on **macOS · Linux · Windows (WSL 2)** and supports the **full Gemma 2 / 3 / 4 suite**.

Built for the [DEV Gemma 4 Challenge](https://dev.to/challenges/gemma4).

---

## How it works

```
YouTube playlists  ──►  yt-dlp (new videos, last 24 h)
                               │
                               ▼
                    faster-whisper  (on-device transcription)
                               │
                               ▼
          ┌──── Gemma Pass 1 ───────────────────────────────────┐
          │  Extract: headline · thesis · pull quotes w/          │
          │  timestamps · stats · predictions · counterpoints ·  │
          │  action items · named entities · open questions       │
          └──────────────────────────────────────────────────────┘
                               │
                               ▼
          ┌──── Gemma Pass 2 ───────────────────────────────────┐
          │  Sharpen: 5 focused follow-up calls — select best    │
          │  quotes, tighten bullets, extract market tickers,    │
          │  FRED macro IDs, Wikipedia terms                     │
          └──────────────────────────────────────────────────────┘
                               │
                               ▼
          ┌──── Gemma Pass 3  (grounding) ─────────────────────┐
          │  Cross-reference enrichers: Yahoo Finance charts,   │
          │  FRED macro sparklines, Wikipedia summaries, live   │
          │  RSS headlines — one grounding sentence per claim   │
          └──────────────────────────────────────────────────────┘
                               │
                      ┌────────┴────────┐
                      ▼                 ▼
              Gotenberg PDF        Markdown vault
              (image-forward       (frontmatter +
               morning brief)       BM25 index)
                      │                 │
                      ▼                 ▼
               Telegram chats    Telegram RAG bot
               (PDF document)    /debate /quiz /find …
```

---

## Quick start

```bash
# 1. Clone + install everything (Ollama, Docker, yt-dlp, ffmpeg — one script)
git clone https://github.com/amadeobonde/gemma-brief
cd gemma-brief
./scripts/install.sh

# 2. Run the setup wizard — pick your Gemma model, add YouTube + Telegram
./.venv/bin/gemma-brief setup

# 3. Start the scheduler + bot (runs 24/7, brief at 02:00 daily)
./.venv/bin/gemma-brief serve
```

The wizard looks like this:

```
  ╔════════════════════════════════════════════════════════╗
  ║          gemma-brief  ·  Setup Wizard                  ║
  ║  Local AI briefing engine — Gemma on-device           ║
  ╚════════════════════════════════════════════════════════╝

  ┌─ Step 1/4  ·  Gemma Model ─────────────────────────────
  │
  │    #   Model              Disk    RAM  Notes
  │    ──────────────────────────────────────────────────────
  │    1   Gemma 4 E2B       5.0 GB    7 GB  Fastest · vision · fits 8 GB RAM
  │  ▶ 2   Gemma 4 E4B       9.6 GB   12 GB  Recommended · vision · 128K ctx  ← recommended
  │    3   Gemma 4 12B      13.0 GB   16 GB  Higher quality · vision · 128K ctx
  │    4   Gemma 4 27B      30.0 GB   35 GB  Max quality · vision · needs 32 GB+
  │    5   Gemma 3 4B        3.3 GB    6 GB  Lightweight · text-only · 128K ctx
  │    6   Gemma 3 12B       8.1 GB   12 GB  Balanced · text-only · 128K ctx
  │    7   Gemma 3 27B      17.0 GB   24 GB  Best text quality · needs 24 GB+
  │    8   Gemma 2 2B        1.7 GB    4 GB  Ultra light · fits any device
  │    9   Gemma 2 9B        5.5 GB    8 GB  Compact · good for 8 GB RAM
  │   10   Gemma 2 27B      16.0 GB   22 GB  Largest Gemma 2 · needs 24 GB+
  │
  │  Select model number [2]:

  ┌─ Step 2/4  ·  System dependencies ─────────────────────
  │
  ✓  Ollama 0.9.1
  ✓  gemma4:e4b  already pulled
  ✓  Docker Desktop 4.40
  ✓  Whisper (port 9000) + Gotenberg (port 3000)  running
  ✓  yt-dlp 2026.05.20
  ✓  ffmpeg found
  │

  ┌─ Step 3/4  ·  Telegram Bot ─────────────────────────────
  │
  ·  Create a bot at t.me/BotFather → /newbot
  ·  Get your chat ID by messaging @userinfobot

  Bot token  (TELEGRAM_BOT_TOKEN)  [hidden] :
  Chat IDs   (comma-separated)     [hidden] :
  │

  ┌─ Step 4/4  ·  Content Sources ──────────────────────────
  │
  ·  Paste YouTube playlist URLs (news, debates, lectures…)
  ·  New videos uploaded in the last 24 h picked up automatically.

  YouTube playlist URL(s):  https://youtube.com/playlist?list=PL...
  │

  ╔════════════════════════════════════════════════════════╗
  ║           ✓  Setup complete!                           ║
  ╠════════════════════════════════════════════════════════╣
  ║                                                        ║
  ║  Model          gemma4:e4b                             ║
  ║  YouTube        2 playlists                            ║
  ║  Telegram       1 chat                                 ║
  ║                                                        ║
  ╚════════════════════════════════════════════════════════╝
```

---

## Gemma model guide

Pick the model that fits your hardware. Re-run `gemma-brief setup` anytime to switch.

| Model | Disk | RAM | Vision | Context | Best for |
|---|---|---|---|---|---|
| `gemma4:e2b` | 5 GB | 7 GB | ✅ | 128K | Fast turnaround, 8 GB machines |
| `gemma4:e4b` | 9.6 GB | 12 GB | ✅ | 128K | **Recommended** — best quality/speed balance |
| `gemma4:12b` | 13 GB | 16 GB | ✅ | 128K | Higher extraction quality |
| `gemma4:27b` | 30 GB | 35 GB | ✅ | 128K | Maximum quality, M2 Ultra / high-RAM workstations |
| `gemma3:4b` | 3.3 GB | 6 GB | ❌ | 128K | Lightweight, text briefs |
| `gemma3:12b` | 8.1 GB | 12 GB | ❌ | 128K | Strong text quality, no vision |
| `gemma3:27b` | 17 GB | 24 GB | ❌ | 128K | Best text-only option |
| `gemma2:2b` | 1.7 GB | 4 GB | ❌ | 8K | Ultra-light, older/low-spec machines |
| `gemma2:9b` | 5.5 GB | 8 GB | ❌ | 8K | Solid compact option |
| `gemma2:27b` | 16 GB | 22 GB | ❌ | 8K | Full Gemma 2 capability |

> **Vision note**: Gemma 4 and Gemma 3 models with vision support caption the video thumbnail during Pass 1 (used as the PDF hero image). Gemma 2 runs text-only and skips the thumbnail caption gracefully.

---

## Platform support

### macOS (Apple Silicon recommended)

```bash
./scripts/install.sh   # installs everything
```

Background service (survives reboots):
```bash
./scripts/install-launchd.sh
launchctl print gui/$UID/com.gemma-brief.serve | grep -E "state|pid"
launchctl kickstart -k gui/$UID/com.gemma-brief.serve   # restart
tail -f logs/gemma-brief.err.log
./scripts/install-launchd.sh uninstall
```

Voice replies use macOS `say` (built-in). Premium neural voices available from System Settings → Accessibility → Spoken Content.

---

### Linux (Ubuntu / Debian / Fedora / Arch)

```bash
./scripts/install.sh   # detects distro, installs ffmpeg via apt/dnf/pacman
```

Background service (systemd):
```bash
./scripts/install-systemd.sh
systemctl --user status gemma-brief
journalctl --user -u gemma-brief -f   # live logs
./scripts/install-systemd.sh uninstall
```

Voice replies use `espeak-ng` (auto-installed on most distros) or `pyttsx3`:
```bash
sudo apt install espeak-ng   # Ubuntu/Debian
sudo dnf install espeak-ng   # Fedora
```

Ollama is auto-installed by `install.sh` on Linux.

---

### Windows (WSL 2)

gemma-brief runs in **WSL 2** (Windows Subsystem for Linux) with full feature parity.

```powershell
# 1. Enable WSL 2 (PowerShell as Administrator)
wsl --install

# 2. Open your Ubuntu terminal, then:
git clone https://github.com/amadeobonde/gemma-brief
cd gemma-brief
./scripts/install.sh
./.venv/bin/gemma-brief setup
./.venv/bin/gemma-brief serve
```

Requirements in WSL 2:
- **Docker Desktop** with the "Use WSL 2 based engine" setting enabled
- **Ollama** running natively on Windows (not inside WSL) — set `OLLAMA_HOST=http://host.docker.internal:11434` in `.env`
- **ffmpeg**: auto-installed via `apt`

Voice replies use `pyttsx3` + Windows SAPI voices (install: `pip install pyttsx3`).

---

## What you can brief

Any public YouTube playlist works:

| Content type | Example |
|---|---|
| News channels | Bloomberg, Reuters, CNBC, BBC |
| Debate shows | Intelligence Squared, Lex Fridman, Huberman |
| Finance / macro | Odd Lots, We Study Billionaires, Monetary Policy |
| Science / tech | Two Minute Papers, MIT OpenCourseWare, 3Blue1Brown |
| Personal playlist | Your "watch later" list, a curated topic playlist |

---

## Telegram bot commands

| Command | What it does |
|---|---|
| `/help` | Full command list |
| `/run` | Reprocess the latest video end-to-end |
| `/debate <claim>` | Gemma steelmans the counter-argument, you rebut |
| `/quiz [topic]` | 3 MCQs from today's brief or a topic across the vault |
| `/flashcard` | One true/false claim with timestamp + explanation |
| `/socratic on\|off` | Toggle a follow-up question on every reply |
| `/find <concept>` | Every vault mention with timestamps and quotes |
| `/numbers` | All figures and stats from today's brief |
| `/contradictions` | Where today's content contradicts older briefs |
| `/connect <topic>` | Cross-video synthesis: today vs vault history |
| `/chart <ticker>` | Live Yahoo price chart + Gemma annotation (native tool calling) |
| `/macro <FRED_id>` | FRED sparkline, e.g. `/macro CPIAUCSL` |
| `/gaps` | Open questions hosts raised but never resolved |
| `/topics` | Recurring themes this week and month |
| `/news <topic>` | Top RSS headlines for a topic, past 7 days |

**On-demand ingest** — send any of these to the bot without a command:
- A **YouTube URL** → duration-aware routing: ≤ 5 min → voice reply, > 5 min → full brief + PDF
- A **voice message** → Whisper STT → RAG answer → TTS → voice bubble back
- An **audio or video file** → same duration-aware routing

---

## What the brief looks like

Each brief is an image-forward PDF with:

- **Hero artwork** (16:9 crop) with accent color sampled from the thumbnail
- **Headline + TL;DR + Thesis** — three-level summary
- **Pull-quote cards** with speaker name and `MM:SS` timestamps
- **Stats panel** — every number mentioned, sourced and labeled
- **Predictions + Counterpoints** — what was claimed, what pushes back
- **Resources** — books, papers, tools, people named in the video
- **Action items** — things you could actually do
- **Market snapshot** — Yahoo Finance 30-day charts for mentioned tickers
- **Macro context** — FRED sparklines for named economic indicators
- **Reality check** — contemporaneous RSS headlines grounded against the brief's claims
- **Similar content** — YouTube search results for related topics

---

## Highlights

- **Three-pass agentic pipeline.** Pass 1 over-extracts (8-12 candidate quotes with self-rated impact scores, standardized identifiers for Yahoo/FRED/Wikipedia, three Socratic questions the host never resolves). Pass 2 sharpens across five focused sub-calls. Pass 3 grounds every claim against real-world data fetched live.
- **Native Gemma tool calling.** The `/chart` command uses Ollama's tool-calling API directly — the model decides to call `get_price_chart`, we resolve it via Yahoo Finance, a second Gemma call annotates the result. See [`podcastbrief/bot/chart_tool.py`](podcastbrief/bot/chart_tool.py).
- **Full Gemma suite.** Runs any `gemma2:*`, `gemma3:*`, or `gemma4:*` model. Context windows auto-tune per family (8K for Gemma 2, 32K for Gemma 3/4). Vision input (thumbnail captioning) activates automatically on Gemma 3/4, gracefully disabled on Gemma 2.
- **Quotes carry timestamps.** Whisper returns segment-level timestamps; the model maps each chosen quote to `MM:SS` so you can jump back to the source.
- **Voice in, voice out.** Send a Telegram voice message → Whisper STT → RAG → TTS (macOS `say` / Linux `espeak-ng` / Windows SAPI) → ffmpeg Opus encode → voice bubble back in Telegram.
- **Obsidian-style vault.** Markdown files with YAML frontmatter, BM25 retrieval, `INDEX.md` and `[[wikilinks]]` auto-maintained. No vector DB, no embeddings cost.
- **Multi-language.** Whisper detects language; the directive flows through every Gemma prompt. Briefs in Spanish get answered in Spanish.
- **Ports & adapters architecture.** Every integration is a `Protocol` in [`podcastbrief/ports/`](podcastbrief/ports/). Swap Whisper for Deepgram, Telegram for Slack, Ollama for a cloud LLM — one implementation, zero pipeline changes.

---

## Requirements

| | macOS | Linux | Windows (WSL 2) |
|---|---|---|---|
| **CPU** | Apple Silicon M1+ recommended; Intel works | x86_64 or arm64 | x86_64 |
| **RAM** | 16 GB (min 8 GB with `gemma4:e2b`) | 16 GB recommended | 16 GB recommended |
| **Python** | 3.11+ (managed by `uv`) | 3.11+ (managed by `uv`) | 3.11+ (managed by `uv`) |
| **Ollama** | Native .pkg installer | Auto-installed by `install.sh` | Native Windows installer |
| **Docker** | Docker Desktop | Docker Engine | Docker Desktop (WSL 2 mode) |

---

## All CLI commands

```bash
gemma-brief setup          # first-run wizard (model picker + credentials)
gemma-brief run-daily      # pull new videos + generate briefs
gemma-brief run-daily --hours 72   # extend look-back window
gemma-brief run-daily --dry-run    # process but skip Telegram + save
gemma-brief run-bot        # Telegram RAG bot (long-running)
gemma-brief serve          # scheduler + bot in one process (recommended)
gemma-brief add --playlist <url>   # register a new YouTube playlist
gemma-brief add --rss <url>        # register an RSS feed
gemma-brief test-brief <audio.mp3> --show "Name" --title "Title"
gemma-brief cleanup-notes  # clear the vault (monthly)
gemma-brief redownload-audio       # backfill audio store
gemma-brief reindex-timestamps     # backfill Whisper word timestamps
```

---

## Run as a background service

### macOS — launchd

```bash
./scripts/install-launchd.sh

# Status
launchctl print gui/$UID/com.gemma-brief.serve | grep -E "state|pid"

# Force restart
launchctl kickstart -k gui/$UID/com.gemma-brief.serve

# Tail logs
tail -f logs/gemma-brief.err.log

# Uninstall
./scripts/install-launchd.sh uninstall
```

### Linux — systemd

```bash
./scripts/install-systemd.sh

# Status
systemctl --user status gemma-brief

# Restart
systemctl --user restart gemma-brief

# Live logs
journalctl --user -u gemma-brief -f

# Uninstall
./scripts/install-systemd.sh uninstall
```

---

## Add more content sources

```bash
# Add another YouTube playlist
gemma-brief add --playlist https://www.youtube.com/playlist?list=PLxxxxxxx

# Add a podcast RSS feed (works alongside YouTube playlists)
gemma-brief add --rss https://feeds.simplecast.com/your-show
```

Or edit `YOUTUBE_PLAYLIST_URLS` (comma-separated) in `.env` directly.

---

## Troubleshooting

**`model requires more system memory than is available`**  
Ollama is probably running inside Docker. Run it natively on the host and set `OLLAMA_HOST=http://127.0.0.1:11434` in `.env` (avoid `localhost` — IPv6 can resolve to the Docker socket). On Windows WSL 2, use `OLLAMA_HOST=http://host.docker.internal:11434`.

**`Connection refused` on Whisper**  
Run `docker compose ps` — check port 9000. Verify `WHISPER_URL=http://localhost:9000` in `.env`.

**`json_complete failed after retries`**  
The model hit its context window. Defaults are `LLM_NUM_CTX=32768` / `LLM_NUM_PREDICT=6144` (set automatically by `gemma-brief setup`). On 8 GB RAM, lower `LLM_NUM_CTX` or switch to `gemma4:e2b` or `gemma3:4b`.

**Bot says "Don't see anything in the briefs"**  
Check the `.md` files have intact YAML frontmatter (`title`, `show`, `date`). The RAG bot reads frontmatter — not the rendered `INDEX.md`.

**yt-dlp fails on a playlist**  
Update yt-dlp: `yt-dlp -U` or `pip install -U yt-dlp`. YouTube changes its API frequently; yt-dlp releases patches within 24–48 h.

**Voice replies not working on Linux**  
Install espeak-ng: `sudo apt install espeak-ng`. Or set `TTS_BACKEND=off` in `.env` to use text-only replies.

**No voice on Windows WSL 2**  
Install pyttsx3: `pip install pyttsx3`. This uses Windows SAPI voices through the WSL bridge.

---

## Architecture

```
podcastbrief/
  core/        models · Pipeline orchestrator · pydantic-settings config
  ports/       9 Protocols — implement to plug in your own services
  adapters/    YouTube · Whisper · Ollama/Gemma · Gotenberg · Telegram
               iTunes artwork · RSS · filesystem notes · YouTube recommender
               Yahoo/FRED/Wikipedia/RSS enrichers
  briefing/    schemas · extractor (pass 1) · interrogator (pass 2)
               grounder (pass 3) · Jinja2 HTML template + CSS
  bot/         RAG (BM25, no vector DB) · voice · debate · chart tool
               17 Telegram command handlers
  jobs/        daily · cleanup · bot · setup wizard · maintenance
  cli.py       Click entry point
docker-compose.yml   faster-whisper + Gotenberg
pyproject.toml
scripts/
  install.sh          one-command install (macOS + Linux)
  install-launchd.sh  macOS background service
  install-systemd.sh  Linux background service
```

---

## The models

### Gemma 4 (recommended)
`gemma4:e4b` — Gemma 4 Effective 4B, Google, April 2026, Apache 2.0
- Text + image input → text output (multimodal)
- 128K context window (we use 32K to keep memory bounded)
- Day-one Ollama support
- Vision used to caption video thumbnails in Pass 1

### Gemma 3
`gemma3:4b` / `gemma3:12b` / `gemma3:27b` — strong text reasoning, 128K context  
No vision; thumbnail captioning skipped automatically.

### Gemma 2
`gemma2:2b` / `gemma2:9b` / `gemma2:27b` — lightweight, 8K context  
Best for older hardware or < 8 GB RAM. Briefs are text-only (no thumbnail caption).

---

## License

MIT. See [LICENSE](LICENSE).
