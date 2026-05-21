from __future__ import annotations
import asyncio
import io as _io
import logging
from pathlib import Path

from podcastbrief.core.config import load_settings
from podcastbrief.adapters.rss_news_enricher import RSSNewsEnricher
from podcastbrief.adapters.wikipedia_enricher import WikipediaEnricher
from podcastbrief.bot.commands import (
    COMMANDS,
    CommandContext,
    handle_flashcard_answer,
    handle_quiz_answer,
)
from podcastbrief.bot.index import ObsidianIndex
from podcastbrief.bot.rag import RagBot
from podcastbrief.bot.voice import VoiceConfig, VoiceProcessor
from podcastbrief.jobs.daily import build_pipeline

log = logging.getLogger(__name__)


def run_bot() -> None:
    """Run the Telegram RAG bot via long-poll.

    Handles:
      - text messages (RagBot)
      - voice messages (Whisper -> RagBot voice mode -> say -> ffmpeg -> sendVoice)
      - 17 slash commands (see bot/commands.py)
      - audio/video file uploads and YouTube URLs (bot/upload_router.py)
      - /run (reprocess most-recent playlist episode)
    """
    s = load_settings()
    logging.basicConfig(level=getattr(logging, s.log_level.upper(), logging.INFO))

    if not s.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")

    pipe = build_pipeline(s)
    rag = RagBot(llm=pipe.llm, notes_dir=s.notes_dir)
    voice = VoiceProcessor(
        transcriber=pipe.transcriber,
        bot_token=s.telegram_bot_token,
        config=VoiceConfig(voice=s.tts_voice, rate=s.tts_rate),
    )
    log.info("TTS voice: %s @ %s wpm", s.tts_voice, s.tts_rate)
    log.info("Voice: OGG/Opus %s", "ENABLED" if voice.has_opus else "DISABLED (m4a fallback)")

    cmd_ctx = CommandContext(
        llm=pipe.llm,
        rag=rag,
        index=ObsidianIndex(base_dir=Path(s.notes_dir)),
        notes_dir=Path(s.notes_dir),
        wiki=WikipediaEnricher(),
        rss=RSSNewsEnricher(feeds=s.rss_feed_list),
    )

    from telegram import Update
    from telegram.ext import (
        Application,
        CommandHandler,
        ContextTypes,
        MessageHandler,
        filters,
    )

    # ---- /run reused from previous commit ----
    async def on_run_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        msg = update.message
        user_id = str(msg.from_user.id) if msg.from_user else "anon"
        log.info("/run from %s", user_id)
        await msg.reply_text(
            "Running the latest playlist episode end-to-end. Fresh download, "
            "fresh transcription, fresh PDF. This takes a few minutes — I'll send "
            "the brief when it's ready."
        )
        loop = asyncio.get_event_loop()

        def _do_run() -> None:
            try:
                ep = pipe.run_latest(dry_run=False)
                if ep is None:
                    asyncio.run_coroutine_threadsafe(
                        msg.reply_text("Playlist is empty — nothing to run."), loop,
                    )
            except Exception as e:
                log.exception("/run failed: %s", e)
                asyncio.run_coroutine_threadsafe(
                    msg.reply_text(f"/run failed: {e}"), loop,
                )

        await asyncio.to_thread(_do_run)

    # ---- /debate: audio voice-note compilation + Gemma analysis ----
    async def on_debate_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        msg = update.message
        user_id = str(msg.from_user.id) if msg.from_user else "anon"
        topic = " ".join(context.args or []).strip()
        log.info("/debate from %s topic=%r", user_id, topic)
        if not topic:
            await msg.reply_text("Usage: /debate <topic>")
            return

        await msg.reply_text("🎙️ Finding debate clips across your vault…")

        from podcastbrief.bot.debate_handler import run_debate
        result = await asyncio.to_thread(
            run_debate,
            topic=topic,
            llm=pipe.llm,
            notes_dir=s.notes_dir,
            audio_store_dir=s.audio_store_path,
            target_dbfs=s.clip_target_dbfs,
            padding_seconds=s.clip_padding_seconds,
            silence_between_ms=s.clip_silence_between_ms,
        )

        if result.error or not result.ogg_bytes:
            await msg.reply_text(result.error or "Couldn't build a debate compilation.")
            return

        try:
            buf = _io.BytesIO(result.ogg_bytes)
            buf.name = "debate.ogg"
            await msg.reply_voice(voice=buf, caption=f"🎙️ Debate: {topic}"[:1024])
        except Exception as e:
            log.exception("Telegram voice send failed: %s", e)
            await msg.reply_text(f"Built the voice note but couldn't send it: {e}")
            return

        if result.analysis_md:
            try:
                await msg.reply_text(result.analysis_md, parse_mode="Markdown")
            except Exception:
                await msg.reply_text(result.analysis_md)
        if result.sources_md:
            await msg.reply_text(result.sources_md)

    # ---- /explain: pull full passage + audio clip for a keyword ----
    async def on_explain_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        msg = update.message
        user_id = str(msg.from_user.id) if msg.from_user else "anon"
        query = " ".join(context.args or []).strip()
        log.info("/explain from %s query=%r", user_id, query)
        if not query:
            await msg.reply_text("Usage: /explain <keyword or topic>  e.g. /explain google spark")
            return

        await msg.reply_text(f"🔍 Searching transcripts for '{query}'…")

        from podcastbrief.bot.explain_handler import run_explain

        result = await asyncio.to_thread(
            run_explain,
            query=query,
            notes_dir=s.notes_dir,
            audio_store_dir=s.audio_store_path,
            target_dbfs=s.clip_target_dbfs,
            padding_seconds=s.clip_padding_seconds,
        )

        if result.error:
            await msg.reply_text(result.error)
            return

        def _fmt(sec: float) -> str:
            s_ = max(0, int(sec))
            h_, s_ = divmod(s_, 3600)
            m_, s_ = divmod(s_, 60)
            return f"{h_}:{m_:02d}:{s_:02d}" if h_ else f"{m_}:{s_:02d}"

        start_ts = _fmt(result.start_seconds)
        end_ts = _fmt(result.end_seconds)

        # Send voice note first so the transcript flows underneath it.
        if result.ogg_bytes:
            try:
                buf = _io.BytesIO(result.ogg_bytes)
                buf.name = f"explain_{result.episode_slug}.ogg"
                caption = f"[[{result.episode_slug}]]  {start_ts} → {end_ts}"[:1024]
                await msg.reply_voice(voice=buf, caption=caption)
            except Exception as e:
                log.exception("/explain voice send failed: %s", e)

        # Verbatim transcript text with header.
        header = (
            f"📝 *{result.episode_title}*\n"
            f"`{start_ts}` → `{end_ts}`\n\n"
        )
        full_text = header + result.transcript_text
        from podcastbrief.bot.commands import _wrap
        for chunk in _wrap(full_text):
            try:
                await msg.reply_text(chunk, parse_mode="Markdown")
            except Exception:
                await msg.reply_text(chunk)

    # ---- Generic command dispatcher ----
    def _make_handler(name: str):
        fn = COMMANDS[name]

        async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            if not update.message:
                return
            msg = update.message
            user_id = str(msg.from_user.id) if msg.from_user else "anon"
            args = (context.args or [])
            log.info("/%s from %s args=%s", name, user_id, args)
            try:
                results = await fn(cmd_ctx, user_id, args)
            except Exception as e:
                log.exception("Command /%s failed: %s", name, e)
                await msg.reply_text(f"/{name} failed: {e}")
                return
            for r in results:
                # Some commands (like /macro) return (text, chart_png) tuples.
                if isinstance(r, tuple) and len(r) == 2 and isinstance(r[1], (bytes, bytearray)):
                    text, png = r
                    if png:
                        buf = _io.BytesIO(png)
                        buf.name = f"{name}.png"
                        await msg.reply_photo(photo=buf, caption=text[:1024])
                    else:
                        await msg.reply_text(text)
                else:
                    await msg.reply_text(str(r))

        return handler

    # ---- Text + voice routing (existing) ----
    async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.message.text:
            return
        user_id = str(update.message.from_user.id) if update.message.from_user else "anon"
        question = update.message.text.strip()

        # Intercept quiz / flashcard answers first.
        quiz_reply = handle_quiz_answer(cmd_ctx, user_id, question)
        if quiz_reply is not None:
            await update.message.reply_text(quiz_reply)
            return
        flash_reply = handle_flashcard_answer(cmd_ctx, user_id, question)
        if flash_reply is not None:
            await update.message.reply_text(flash_reply)
            return

        # Quick check: bare YouTube URL routes to the upload handler.
        from podcastbrief.bot.upload_router import (
            is_youtube_url,
            handle_youtube_or_upload,
        )
        if is_youtube_url(question):
            await handle_youtube_or_upload(
                update, context,
                url_or_file_id=question,
                voice=voice,
                rag=rag,
                pipe=pipe,
                source="youtube_url",
            )
            return

        log.info("RAG text from %s: %s", user_id, question[:80])
        try:
            answer = rag.answer(user_id=user_id, question=question)
        except Exception as e:
            log.exception("RAG answer failed: %s", e)
            answer = "Hit an issue. Try again in a moment."

        # Socratic mode: append a follow-up question.
        if cmd_ctx.socratic.get(user_id):
            try:
                followup = await asyncio.to_thread(
                    pipe.llm.complete,
                    system=(
                        "Add ONE sharp follow-up question that pushes the user to "
                        "think harder about the previous answer. Return ONLY the "
                        "question, nothing else. No preamble."
                    ),
                    user=f"Previous answer:\n{answer}",
                    temperature=0.6,
                )
                answer = f"{answer}\n\n— {followup.strip()}"
            except Exception:
                pass
        await update.message.reply_text(answer)

    async def on_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.message.voice:
            return
        msg = update.message
        user_id = str(msg.from_user.id) if msg.from_user else "anon"
        file_id = msg.voice.file_id
        duration = msg.voice.duration or 0
        log.info("RAG voice from %s: file_id=%s duration=%ds", user_id, file_id, duration)

        try:
            audio_bytes = voice.download_voice(file_id)
            await context.bot.send_chat_action(chat_id=msg.chat_id, action="typing")
            question = voice.transcribe(audio_bytes)
            if not question:
                await msg.reply_text("Couldn't make out the audio. Try again?")
                return
            log.info("Transcribed: %s", question[:120])
        except Exception as e:
            log.exception("Voice STT failed: %s", e)
            await msg.reply_text("Couldn't process the voice message. Try again?")
            return

        try:
            answer = rag.answer(user_id=user_id, question=question, mode="voice")
        except Exception as e:
            log.exception("RAG answer failed: %s", e)
            await msg.reply_text("Hit an issue answering that. Try again in a moment.")
            return

        try:
            await context.bot.send_chat_action(chat_id=msg.chat_id, action="record_voice")
            audio_bytes, mime, fname = voice.synthesize(answer)
        except Exception as e:
            log.exception("TTS failed: %s", e)
            await msg.reply_text(answer)
            return

        buf = _io.BytesIO(audio_bytes)
        buf.name = fname
        try:
            if mime == "audio/ogg":
                await msg.reply_voice(voice=buf)
            else:
                await msg.reply_audio(audio=buf, title="Reply")
        except Exception as e:
            log.exception("Telegram audio send failed: %s", e)
            await msg.reply_text(answer)

    # ---- Audio/video file uploads (item 6) ----
    async def on_audio_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        from podcastbrief.bot.upload_router import handle_youtube_or_upload
        msg = update.message
        if not msg:
            return
        await handle_youtube_or_upload(
            update, context,
            url_or_file_id=None,
            voice=voice,
            rag=rag,
            pipe=pipe,
            source="upload",
        )

    app = Application.builder().token(s.telegram_bot_token).build()
    # Custom-handled commands first.
    app.add_handler(CommandHandler("run", on_run_command))
    app.add_handler(CommandHandler("debate", on_debate_command))
    app.add_handler(CommandHandler("explain", on_explain_command))
    # Generic command suite.
    for name in COMMANDS:
        app.add_handler(CommandHandler(name, _make_handler(name)))
    # Audio / video / document uploads
    app.add_handler(MessageHandler(filters.AUDIO | filters.VIDEO | filters.Document.AUDIO, on_audio_upload))
    # Voice + text last so they don't shadow the commands.
    app.add_handler(MessageHandler(filters.VOICE, on_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    log.info("Telegram bot polling started (text + voice + uploads + commands + /run + /debate + /explain).")
    app.run_polling(allowed_updates=Update.ALL_TYPES)
