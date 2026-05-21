# gemma-brief

**Local AI briefing engine for YouTube, news & debates.**  
Point it at any YouTube playlist — news channels, debate shows, lectures, podcasts. It transcribes every new video with Whisper, runs three passes of Gemma 4 analysis, and sends you a structured PDF brief via Telegram. Then lets you ask questions, run debates, and quiz yourself across everything in your library.

Everything runs **fully on-device**. No OpenAI. No subscriptions. No data leaving your machine.

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
          ┌──── Gemma 4 Pass 1 ─────────────────────────────────┐
          │  Extract: headline · thesis · pull quotes w/          │
          │  timestamps · stats · predictions · counterpoints ·  │
          │  action items · named entities · open questions       │
          └──────────────────────────────────────────────────────┘
                               │
                               ▼
          ┌──── Gemma 4 Pass 2 ─────────────────────────────────┐
          │  Sharpen: 5 focused follow-up calls — select best    │
          │  quotes, tighten bullets, extract market tickers,    │
          │  FRED macro IDs, Wikipedia terms                     │
          └──────────────────────────────────────────────────────┘
                               │
                               ▼
          ┌──── Gemma 4 Pass 3  (grounding) ───────────────────┐
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

# 2. Run the setup wizard — adds your YouTube URLs + Telegram bot in 3 steps
./.venv/bin/gemma-brief setup

# 3. Start the scheduler + bot (runs 24/7, brief at 02:00 daily)
./.venv/bin/gemma-brief serve
```

The wizard looks like this:

```
  ╔════════════════════════════════════════════════════════╗
  ║          gemma-brief  ·  Setup Wizard                  ║
  ║  Local AI briefing engine — Gemma 4 on-device         ║
  ╚════════════════════════════════════════════════════════╝

  ┌─ Step 1/3  ·  System dependencies ─────────────────────
  │
  ✓  Ollama 0.9.1
  ✓  gemma4:e4b  already pulled
  ✓  Docker Desktop 4.40
  ✓  Whisper (port 9000) + Gotenberg (port 3000)  running
  ✓  yt-dlp 2026.05.20
  ✓  ffmpeg found
  │

  ┌─ Step 2/3  ·  Telegram Bot ─────────────────────────────
  │
  ·  Create a bot at t.me/BotFather → /newbot
  ·  Get your chat ID by messaging @userinfobot

  Bot token  (TELEGRAM_BOT_TOKEN)  [hidden] : 
  Chat IDs   (comma-separated)     [hidden] : 
  │

  ┌─ Step 3/3  ·  Content Sources ──────────────────────────
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

  Start the service:   gemma-brief serve
  Run once now:        gemma-brief run-daily
```

---

## What you can brief

Any public YouTube playlist works. Some ideas:

| Content type | Example |
|---|---|
| News channels | Bloomberg, Reuters, CNBC, BBC |
| Debate shows | Intelligence Squared, Lex Fridman, Huberman |
| Finance / macro | Odd Lots, We Study Billionaires, Monetary Policy |
| Science / tech | Two Minute Papers, MIT OpenCourseWare, 3Blue1Brown |
| Personal playlist | Your "watch later" list, a curated topic playlist |

---

## Telegram bot commands

Once the bot is running, you can interact with everything in your brief library:

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
- A **voice message** → Whisper STT → RAG answer → macOS TTS → voice bubble back
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
- **Native Gemma 4 tool calling.** The `/chart` command uses Ollama's tool-calling API directly — the model decides to call `get_price_chart`, we resolve it via Yahoo Finance, a second Gemma call annotates the result. See [`podcastbrief/bot/chart_tool.py`](podcastbrief/bot/chart_tool.py).
- **Quotes carry timestamps.** Whisper returns segment-level timestamps; the model maps each chosen quote to `MM:SS` so you can jump back to the source.
- **Voice in, voice out.** Send a Telegram voice message → Whisper STT → RAG → macOS `say` (premium neural voices) → ffmpeg Opus encode → voice bubble back in Telegram.
- **Obsidian-style vault.** Markdown files with YAML frontmatter, BM25 retrieval, `INDEX.md` and `[[wikilinks]]` auto-maintained. No vector DB, no embeddings cost.
- **Multi-language.** Whisper detects language; the directive flows through every Gemma prompt. Briefs in Spanish get answered in Spanish.
- **Ports & adapters architecture.** Every integration is a `Protocol` in [`podcastbrief/ports/`](podcastbrief/ports/). Swap Whisper for Deepgram, Telegram for Slack, Ollama for a cloud LLM — one implementation, zero pipeline changes.

---

## Requirements

| | |
|---|---|
| **macOS** | Apple Silicon (M1+) recommended. Intel Macs work but are slower. |
| **RAM** | 16 GB minimum. Gemma 4 E4B needs ~10 GB for the model + KV cache. On 8 GB, use `LLM_MODEL=gemma4:e2b`. |
| **Python** | 3.11+ (the install script handles this via `uv`) |
| **Ollama** | Running natively on the host (not in Docker) so the model gets full RAM |
| **Docker** | For Whisper (transcription) and Gotenberg (PDF rendering) |

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

## All CLI commands

```bash
gemma-brief setup          # first-run wizard
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

## Run as a macOS background service

Install as a launchd agent so `gemma-brief serve` auto-starts on login and restarts on crash:

```bash
./scripts/install-launchd.sh
```

Then:

```bash
# Status
launchctl print gui/$UID/com.gemma-brief.serve | grep -E "state|pid"

# Force restart
launchctl kickstart -k gui/$UID/com.gemma-brief.serve

# Tail logs
tail -f logs/gemma-brief.err.log

# Uninstall
./scripts/install-launchd.sh uninstall
```

---

## Troubleshooting

**`model requires more system memory than is available`**  
Ollama is probably running inside Docker. Run it natively on the host and set `OLLAMA_HOST=http://127.0.0.1:11434` in `.env` (avoid `localhost` — IPv6 can resolve to the Docker socket).

**`Connection refused` on Whisper**  
Run `docker compose ps` — check port 9000. Verify `WHISPER_URL=http://localhost:9000` in `.env`.

**`json_complete failed after retries`**  
The model hit its context window. Defaults are `num_ctx=32768` / `num_predict=6144` (see [`podcastbrief/adapters/ollama_gemma.py`](podcastbrief/adapters/ollama_gemma.py)). On 8 GB RAM, lower `num_ctx` or switch to `gemma4:e2b`.

**Bot says "Don't see anything in the briefs"**  
Check the `.md` files have intact YAML frontmatter (`title`, `show`, `date`). The RAG bot reads frontmatter — not the rendered `INDEX.md`.

**yt-dlp fails on a playlist**  
Update yt-dlp: `yt-dlp -U` or `pip install -U yt-dlp`. YouTube changes its API frequently; yt-dlp releases patches within 24–48 h.

---

## Architecture

```
podcastbrief/
  core/        models · Pipeline orchestrator · pydantic-settings config
  ports/       9 Protocols — implement to plug in your own services
  adapters/    YouTube · Whisper · Ollama/Gemma 4 · Gotenberg · Telegram
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
scripts/install.sh   one-command install
```

---

## The model

`gemma4:e4b` — Gemma 4 Effective 4B, Google, April 2026, Apache 2.0

- Text + image input → text output (multimodal)
- 128K context window (we use 32K to keep memory bounded)
- Runs on Apple Silicon with ~10 GB RAM
- Day-one Ollama support

We use the vision capability on a sub-call during Pass 1 to caption the video thumbnail; the same thumbnail becomes the PDF hero image.

---

## License

MIT. See [LICENSE](LICENSE).
