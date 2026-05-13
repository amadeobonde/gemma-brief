# podcastbrief

A modular Python pipeline that turns a Spotify playlist of podcast episodes into image-forward "morning brief" PDFs and an Obsidian-style notes vault you can chat with from Telegram. Powered locally by **Gemma 4 E4B** (Google's multimodal open model, 128K context, Apr 2026) running via Ollama.

Replaces a fragile n8n workflow with a code-first architecture you can attach your own APIs to — every external integration is a `Protocol` you can swap.

## What it does

```
[Daily 02:00] Spotify playlist
        │
        ▼
last-24h episodes  ──► iTunes RSS lookup ──► download MP3
                                                   │
                                                   ▼
                                        faster-whisper (verbose_json + segment timestamps)
                                                   │
                                                   ▼
                                       Pass 1: Gemma 4 extract structured JSON
                                       (TLDR, thesis, 8-12 candidate quotes,
                                        numbers, hosts, guests, resources,
                                        predictions, counterpoints, action items)
                                       + vision sub-call on episode artwork
                                                   │
                                                   ▼
                                       Pass 2: Gemma 4 self-interrogation (5 sub-calls)
                                       (quote selection, bullet sharpening,
                                        data enrichment, resources sweep, headline)
                                                   │
                                                   ▼
                                       Spotify recommend (similar episodes)
                                                   │
                                                   ▼
                                       Jinja2 + Gotenberg → image-forward PDF brief
                                                   │
                          ┌────────────────────────┼────────────────────────┐
                          ▼                        ▼                        ▼
                     Telegram chats          markdown vault            INDEX.md
                     (PDF document)          (frontmatter + body)      (Obsidian-style)

[Telegram bot] user message ──► full-body retrieval over the vault ──► Gemma 4 reply
[Monthly 1st 03:00] cleanup vault
```

## Highlights

- **Three-pass agentic pipeline.** Pass 1 over-extracts a rich structured representation (8-12 candidate quotes with self-rated impact scores, numbers, predictions, plus standardized identifiers: Yahoo tickers, FRED series IDs, Wikipedia search terms, three open "socratic" questions the host never resolves). Pass 2 runs five focused follow-up calls to sharpen and select. Pass 3 grounds the brief against real-world data fetched by the enrichers — one sentence per chart/indicator/headline comparing what the host predicted vs what actually happened.
- **Live enrichment** during every run: Yahoo Finance 30-day price charts per ticker, FRED 12-month sparklines per macro indicator, Wikipedia summaries for named entities, contemporaneous RSS headlines from Reuters/FT/BBC scored by keyword overlap with the episode. All cached to a JSON sidecar so reruns are fast.
- **Quotes carry timestamps.** Whisper returns segment timestamps; the model maps each chosen quote to `MM:SS` so you can jump back to it in the audio.
- **Image-forward "morning brief" PDF.** Hero artwork (16:9 crop), accent color sampled from the artwork via Pillow, large pull-quote cards with timestamps, stat cards, conditional sections for predictions, counterpoints, resources, action items, similar episodes, market snapshot, macro context, key concepts, and a "Reality Check" panel with the grounded RSS headlines.
- **Obsidian-style RAG bot.** Markdown files are the source of truth — frontmatter + full body retrieval over BM25, no vector DB. `INDEX.md` and `[[wikilinks]]` auto-maintained.
- **Voice in, voice out.** Send a Telegram voice message — Whisper STT → RagBot in voice mode → macOS `say` (premium neural voices like Ava) → ffmpeg Opus → Telegram voice bubble.
- **17 Telegram commands:** /quiz, /flashcard, /retention, /socratic, /debate, /challenge, /connect, /find, /numbers, /contradictions, /define, /news, /chart, /macro, /topics, /gaps, /help — see `/help` in the bot or `podcastbrief/bot/commands.py`.
- **On-demand ingest.** Send a YouTube URL or upload an audio/video file to the bot. ≤5 minutes → conversational voice reply. >5 minutes → full pipeline + PDF brief.
- **Native Gemma 4 function calling** on the `/chart` command — Ollama tool-calling API, not text parsing. The model decides to call `get_price_chart`, we resolve it via Yahoo, then a second Gemma call annotates. See `podcastbrief/bot/chart_tool.py` for the reference implementation.
- **Multi-language.** Whisper detects the transcript language; the directive flows through every Gemma prompt (extractor, interrogator, grounder, bot text + voice). Briefs in Spanish get answered in Spanish.
- **Ports & adapters.** Implement any of 10 `Protocol`s in `podcastbrief/ports/` to plug your own service in (Spotify → Apple Podcasts, Whisper → Deepgram, Ollama → OpenAI, Gotenberg → WeasyPrint, Telegram → Slack, Enricher → your own data source, etc.).

## Architecture

```
podcastbrief/
  core/        models, Pipeline orchestrator, pydantic-settings config
  ports/       9 Protocols — what to implement to attach your APIs
  adapters/    default implementations (Spotify, iTunes/RSS, Whisper, Ollama,
               iTunes artwork, Gotenberg, Telegram, FS notes, Spotify recommend)
  briefing/    schemas, extractor (pass 1), interrogator (pass 2), Jinja2 template
  bot/         Obsidian-style RAG (frontmatter + BM25, no vector DB)
               + voice.py: Whisper STT + macOS say TTS + ffmpeg Opus encode
  jobs/        daily, cleanup, bot, auth-spotify CLI entry points
  cli.py       Click commands
docker-compose.yml   Whisper + Gotenberg
pyproject.toml
.env.example
```

## Bot command reference

| Command | What it does |
| --- | --- |
| `/help` | Print the full command list. |
| `/run` | Reprocess the most-recently-added playlist episode end-to-end. Dedup-aware. |
| `/quiz [topic]` | 3 MCQs from today's brief (or topic across the vault). Reply A/B/C/D. |
| `/flashcard` | One true/false claim from today's brief with timestamp + explanation. |
| `/retention` | Quiz history: accuracy, streak, weakest concepts. |
| `/socratic on\|off` | Toggle a follow-up question on every reply. |
| `/debate <claim>` | Gemma steelmans the counter, you rebut. |
| `/challenge` | The weakest episode argument — defend or attack. |
| `/connect <topic>` | Cross-episode synthesis: today vs vault history. |
| `/find <concept>` | Every vault mention with timestamps and quotes. |
| `/numbers` | All figures and stats from today's brief. |
| `/contradictions` | Where today contradicts older briefs. |
| `/define <concept>` | Wikipedia summary (live). |
| `/news <topic>` | Top 3 RSS headlines for a topic, past 7 days. |
| `/chart <ticker>` | Live Yahoo chart + Gemma annotation. Uses native Gemma 4 tool calling. |
| `/macro <FRED_id>` | FRED sparkline + latest value, e.g. `/macro CPIAUCSL`. |
| `/topics` | Recurring themes this week and this month. |
| `/gaps` | Open questions hosts raised but didn't resolve. |

Voice messages, audio uploads, and YouTube URLs are all accepted in addition to commands — see "On-demand ingest" below.

## Voice messages

Send a Telegram voice message to the bot and it will:

1. Download the OGG/Opus from Telegram
2. Transcribe it with the same `faster-whisper` container the daily pipeline uses
3. Answer via the same RAG pipeline (full-body vault retrieval)
4. Render the answer with macOS `say` (default voice: `Samantha`, 185 wpm), encode to OGG/Opus via ffmpeg at 32 kbps voip profile
5. Reply with `sendVoice` so it shows up as a voice bubble

To change the voice or rate, set `TTS_VOICE` and `TTS_RATE` in `.env`. For natural output, download Apple's premium neural voices via **System Settings → Accessibility → Spoken Content → System Voice → Manage Voices…**, then set `TTS_VOICE="Ava (Premium)"` (or Zoe / Evan / Allison / Siri Voice 1-5).

## On-demand ingest

Send any of these to the Telegram bot and it routes automatically:

- **A voice message** → conversational voice-bubble reply (Whisper → RagBot voice mode → TTS).
- **An audio or video file** (MP3, M4A, OGG, MP4, WAV) → duration-aware:
  - **≤ 5 minutes** → conversational voice reply (treated as a question).
  - **> 5 minutes** → full pipeline (Whisper, Pass 1/2/3, enrichment, PDF brief sent to the chat).
- **A YouTube URL** → same duration-aware routing, audio pulled via `yt-dlp`.

The full-pipeline path participates in vault dedup: re-uploading the same episode replaces the existing note rather than duplicating it.

## Benchmarks

```bash
./.venv/bin/python scripts/benchmark.py            # last 10 briefs
./.venv/bin/python scripts/benchmark.py --limit 5  # quick smoke test
```

Pits single-shot summarization against the existing two-pass architecture across the vault, scored by Gemma 4 on three 1-10 criteria (claim accuracy, quote relevance, actionability). Writes a markdown table to `./benchmarks/results.md`.

## Tested on

The pipeline is developed and run 24/7 on:

- **Mac mini (Mac16,10, 2024)** — Apple M4, 10 cores (4P + 6E), **16 GB unified memory**
- **macOS 15.6** (Sequoia)

That's the minimum we'd recommend — Gemma 4 E4B needs ~10 GB just for the model, plus KV cache for a 32K context window, plus headroom for everything else (Whisper, Gotenberg, the bot, the OS). On 8 GB you'll need to drop to `gemma4:e2b`.

## Requirements

- **Python 3.11+** (we ship for 3.11 — install via [uv](https://docs.astral.sh/uv/) if your system Python is older)
- **Ollama** running natively on your host (not in Docker, so the model gets full RAM): https://ollama.com
- **Docker** (for the Whisper + Gotenberg services)
- **~16 GB RAM** to comfortably run Gemma 4 E4B (~10 GB model + KV cache)
- A **Spotify Developer app** and a **Telegram bot token**

## Quick install (macOS)

One command does everything except the Spotify OAuth and Telegram bot creation steps:

```bash
./scripts/install.sh
```

That script installs uv, creates the Python 3.11 venv, installs the package, ensures Ollama is present and pulls `gemma4:e4b`, brings up the Whisper + Gotenberg docker compose services, drops standalone `ffmpeg` and `yt-dlp` binaries into `~/.local/bin`, copies `.env.example` to `.env` if missing, and prints the remaining setup steps. Idempotent — safe to re-run.

If you'd rather do it by hand, the explicit steps follow.

## One-time setup

### 1. Install Python 3.11 (if needed) and create a venv

```bash
# If you don't have Python 3.11+:
curl -LsSf https://astral.sh/uv/install.sh | sh
uv venv --python 3.11 .venv

# Otherwise:
python3.11 -m venv .venv
```

### 2. Install the package

```bash
source .venv/bin/activate
pip install -e .
```

### 3. Pull the model

```bash
ollama pull gemma4:e4b
```

(Or set `LLM_MODEL=gemma4:e2b` in `.env` if you have less than 12 GB RAM available.)

### 4. Start Whisper + Gotenberg

```bash
docker compose up -d
```

This brings up:
- `faster-whisper` on `http://localhost:9000` (transcription)
- `gotenberg` on `http://localhost:3000` (HTML → PDF)

### 4a. (Optional) Install ffmpeg for voice replies

Telegram voice messages need OGG/Opus encoding. macOS doesn't ship ffmpeg; the
quickest non-Homebrew route is the static arm64 build:

```bash
curl -fsSL https://www.osxexperts.net/ffmpeg71arm.zip -o /tmp/ffmpeg.zip
unzip -o /tmp/ffmpeg.zip -d /tmp/ffmpeg-extract
cp /tmp/ffmpeg-extract/ffmpeg ~/.local/bin/ffmpeg
chmod +x ~/.local/bin/ffmpeg
xattr -d com.apple.quarantine ~/.local/bin/ffmpeg 2>/dev/null || true
```

Without ffmpeg the bot still answers voice messages, just as `sendAudio` M4A
files instead of `sendVoice` bubbles.

### 5. Create a Spotify app

1. Go to https://developer.spotify.com/dashboard and create a new app.
2. Copy the **Client ID** and **Client Secret**.
3. In **Settings → Redirect URIs**, add exactly: `http://127.0.0.1:3000/discovery`.

### 6. Create a Telegram bot

1. Open `@BotFather` in Telegram, send `/newbot`, follow the prompts.
2. Save the bot token.
3. Send `/start` to your new bot from each Telegram account that should receive briefs.
4. Get each chat ID by sending any message to `@userinfobot`.

### 7. Configure `.env`

```bash
cp .env.example .env
# Edit .env and fill in:
#   SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, SPOTIFY_PLAYLIST_ID
#   TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_IDS
```

### 8. One-time Spotify OAuth

This grabs a long-lived refresh token using the redirect URI you registered.

> Stop Gotenberg first if you registered it on port 3000 (the auth flow needs that port temporarily): `docker stop podcastbrief-gotenberg`.

```bash
podcastbrief auth-spotify --write-env
```

Your browser will open. Click **Agree**. The token gets written into `.env` automatically.

```bash
docker start podcastbrief-gotenberg
```

## Running

```bash
# Daily run — pulls last-24h episodes, generates briefs, sends to Telegram
podcastbrief run-daily

# Override the look-back window
podcastbrief run-daily --hours 72

# Dry run — process episodes but skip Telegram + note save
podcastbrief run-daily --dry-run

# Telegram RAG bot (long-running)
podcastbrief run-bot

# Monthly cleanup (clears the vault)
podcastbrief cleanup-notes

# All-in-one: scheduler (02:00 daily, 03:00 first-of-month) + bot in one process
podcastbrief serve

# Test-render a brief from a local audio file (skips Spotify + Telegram)
podcastbrief test-brief ./tests/fixtures/sample.mp3 \
  --show "Show Name" --title "Episode Title" \
  --artwork ./tests/fixtures/art.jpg \
  --out ./test_brief.pdf
```

## Run as a system service (macOS launchd) — recommended for Mac mini

This is how it runs on the test Mac mini: `podcastbrief serve` is supervised by **launchd** as a LaunchAgent so it auto-starts on user login and **auto-restarts if the process or the machine ever crashes**.

```bash
./scripts/install-launchd.sh
```

That script:
1. Creates `~/Library/LaunchAgents/com.podcastbrief.serve.plist` from the template at `scripts/com.podcastbrief.serve.plist`, substituting the project's absolute path.
2. Bootstraps it into your `gui/$UID` domain.
3. Kickstarts it immediately.

Properties of the agent:
- `RunAtLoad = true` → starts on every user login
- `KeepAlive = true` → respawns on any exit (including crashes, panics, oom-kills)
- `ThrottleInterval = 30` → minimum 30 s between restarts so a bug can't burn CPU in a tight loop
- `ProcessType = Background` → low priority, won't interfere with foreground apps
- stdout/stderr → `./logs/podcastbrief.{out,err}.log`

**For the Mac mini to come back up cleanly after a power loss or kernel panic**, also enable in macOS **System Settings → General → Login Items & Extensions**:
- Auto-login on boot for the user that owns this checkout (System Settings → Users & Groups → Automatic login as)
- "Start up automatically after a power failure" (System Settings → Energy)
- "Restart automatically if the computer freezes" (older macOS) / Wake-on-LAN if you want remote recovery

With those toggles + `KeepAlive`, the service is up at all times.

### Operator commands

```bash
# Status
launchctl print gui/$UID/com.podcastbrief.serve | grep -E "state|pid|last exit"

# Force restart
launchctl kickstart -k gui/$UID/com.podcastbrief.serve

# Tail logs
tail -f logs/podcastbrief.err.log

# Uninstall
./scripts/install-launchd.sh uninstall
```

### Alternative: cron / systemd

If you don't want the long-running `serve`, schedule the discrete CLIs:

```cron
0 2 * * *  cd /path/to/podcastbrief && /path/to/.venv/bin/podcastbrief run-daily
0 3 1 * *  cd /path/to/podcastbrief && /path/to/.venv/bin/podcastbrief cleanup-notes
@reboot    cd /path/to/podcastbrief && /path/to/.venv/bin/podcastbrief run-bot >> bot.log 2>&1
```

## Attaching your own APIs

Every integration is a `Protocol` in `podcastbrief/ports/`. Implement it, pass an instance into the `Pipeline` constructor in `podcastbrief/jobs/daily.py`, and you're done. Examples:

| Port | Default | Swap for |
| --- | --- | --- |
| `PodcastSource` | `SpotifySource` | Apple Podcasts, Pocket Casts, custom queue |
| `FeedResolver` | `ItunesRssFeed` (iTunes search → RSS → title-match) | Direct RSS, Listen Notes, your catalog |
| `Transcriber` | `WhisperHttpTranscriber` (faster-whisper) | OpenAI Whisper, Deepgram, AssemblyAI |
| `LLM` | `OllamaGemma` (Gemma 4 E4B) | OpenAI, vLLM, any cloud or local LLM |
| `ImageProvider` | `ItunesArtworkProvider` (1200×1200) | Spotify, your CDN, generated artwork |
| `BriefRenderer` | `GotenbergRenderer` | `WeasyPrintRenderer` (in repo), Playwright, custom |
| `Notifier` | `TelegramNotifier` | Slack, Discord, email, webhook |
| `NoteStore` | `FilesystemNoteStore` (markdown + INDEX.md) | Notion, Logseq, your DB |
| `Recommender` | `SpotifyEpisodeRecommender` | Listen Notes, your own embeddings |

## Troubleshooting

- **`model requires more system memory than is available`** — your Ollama is likely running inside Docker (`docker stats` shows the container memory cap). Run Ollama natively on the host so it can use full system RAM, and set `OLLAMA_HOST=http://127.0.0.1:11434` (avoid `localhost` — IPv6 resolution can prefer the Docker socket).
- **`Connection refused` on Whisper** — check `docker compose ps`, verify `WHISPER_URL` matches the host port (default 9000).
- **Spotify auth: `INVALID_REDIRECT_URI`** — the URI in your Spotify app's Redirect URI list must match `http://127.0.0.1:3000/discovery` exactly. Stop Gotenberg before running `auth-spotify` so port 3000 is free.
- **`json_complete failed after retries`** — usually means the model is hitting the wrong context window. Defaults are `num_ctx=32768` / `num_predict=6144` (see `adapters/ollama_gemma.py`). On smaller models, lower these.
- **Bot says "Don't see anything in the briefs"** even though the topic is in a brief — check that frontmatter is intact in the `.md` files (`title`, `show`, `date`). The bot reads frontmatter, not the rendered `INDEX.md`.

## Notes on the model

`gemma4:e4b` (Effective 4B, edge-tuned, multimodal):

- Released 2026-04-02, Apache 2.0
- Text + image input → text output
- 128K context (we use 32K to keep memory bounded)
- Day-one Ollama support

We use the vision capability for an artwork-caption sub-call during pass 1; the brief embeds the same artwork as the hero image.

## License

MIT. See [LICENSE](LICENSE).
