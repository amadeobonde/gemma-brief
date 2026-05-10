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

- **Two-pass self-questioning summarizer.** Pass 1 over-extracts (8-12 candidate quotes with self-rated impact scores, numbers, resources, predictions). Pass 2 runs five focused follow-up calls — quote selection, bullet sharpening, data enrichment, resources sweep, headline. Dramatically better than single-shot prompts. Each sub-call has a fallback so a flaky LLM response can't kill the run.
- **Quotes carry timestamps.** Whisper returns segment timestamps; the model maps each chosen quote to `MM:SS` so you can jump back to it in the audio.
- **Image-forward "morning brief" PDF.** Hero artwork (16:9 crop with show-color gradient), accent color sampled from the artwork via Pillow, large pull-quote cards with timestamps, stat cards, conditional sections (predictions, counterpoints, resources, action items, similar episodes).
- **Obsidian-style RAG bot.** Markdown files are the source of truth. The bot reads frontmatter + full body content (transcript stripped for token budget), so "give me 3 quotes" actually returns 3 verbatim quotes. `INDEX.md` and `[[wikilinks]]` between briefs that share topics are auto-maintained.
- **Ports & adapters.** Implement any of 9 `Protocol`s in `podcastbrief/ports/` to plug your own service in (Spotify → Apple Podcasts, Whisper → Deepgram, Ollama → OpenAI, Gotenberg → WeasyPrint, Telegram → Slack, etc.).

## Architecture

```
podcastbrief/
  core/        models, Pipeline orchestrator, pydantic-settings config
  ports/       9 Protocols — what to implement to attach your APIs
  adapters/    default implementations (Spotify, iTunes/RSS, Whisper, Ollama,
               iTunes artwork, Gotenberg, Telegram, FS notes, Spotify recommend)
  briefing/    schemas, extractor (pass 1), interrogator (pass 2), Jinja2 template
  bot/         Obsidian-style RAG (frontmatter + BM25, no vector DB)
  jobs/        daily, cleanup, bot, auth-spotify CLI entry points
  cli.py       Click commands
docker-compose.yml   Whisper + Gotenberg
pyproject.toml
.env.example
```

## Requirements

- **Python 3.11+** (we ship for 3.11 — install via [uv](https://docs.astral.sh/uv/) if your system Python is older)
- **Ollama** running natively on your host (not in Docker, so the model gets full RAM): https://ollama.com
- **Docker** (for the Whisper + Gotenberg services)
- **~16 GB RAM** to comfortably run Gemma 4 E4B (~10 GB model + KV cache)
- A **Spotify Developer app** and a **Telegram bot token**

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

## Cron / launchd / systemd

If you don't want `serve` always running, schedule the CLI directly:

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
