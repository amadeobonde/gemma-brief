from __future__ import annotations
import asyncio
import logging
from podcastbrief.core.config import load_settings
from podcastbrief.bot.rag import RagBot
from podcastbrief.bot.voice import VoiceProcessor, VoiceConfig
from podcastbrief.jobs.daily import build_pipeline

log = logging.getLogger(__name__)


def run_bot() -> None:
    """Run the Telegram RAG bot via long-poll.

    Handles text, voice, and slash commands:
      /run — reprocesses the most-recently-added playlist episode end-to-end
              (fresh Whisper, fresh Gemma, fresh PDF), dedup-aware so the
              vault never duplicates entries.
    """
    s = load_settings()
    logging.basicConfig(level=getattr(logging, s.log_level.upper(), logging.INFO))

    if not s.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")

    # Share one Pipeline across the bot so /run uses identical code paths to
    # the daily scheduled run, and voice STT reuses the same Whisper instance.
    pipe = build_pipeline(s)
    rag = RagBot(llm=pipe.llm, notes_dir=s.notes_dir)
    voice = VoiceProcessor(
        transcriber=pipe.transcriber,
        bot_token=s.telegram_bot_token,
        config=VoiceConfig(voice=s.tts_voice, rate=s.tts_rate),
    )
    log.info("TTS voice: %s @ %s wpm", s.tts_voice, s.tts_rate)
    log.info("Voice: OGG/Opus output %s", "ENABLED" if voice.has_opus else "DISABLED (m4a fallback)")

    from telegram import Update
    from telegram.ext import (
        Application,
        CommandHandler,
        MessageHandler,
        filters,
        ContextTypes,
    )

    async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.message.text:
            return
        user_id = str(update.message.from_user.id) if update.message.from_user else "anon"
        question = update.message.text.strip()
        log.info("RAG text from %s: %s", user_id, question[:80])
        try:
            answer = rag.answer(user_id=user_id, question=question)
        except Exception as e:
            log.exception("RAG answer failed: %s", e)
            answer = "Hit an issue. Try again in a moment."
        await update.message.reply_text(answer)

    async def on_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.message.voice:
            return
        msg = update.message
        user_id = str(msg.from_user.id) if msg.from_user else "anon"
        file_id = msg.voice.file_id
        log.info("RAG voice from %s: file_id=%s duration=%ds", user_id, file_id, msg.voice.duration)

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

        import io as _io
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

    async def on_run_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Reprocess the most-recently-added playlist episode end-to-end."""
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

        # Run the blocking pipeline off the event loop so polling stays responsive.
        def _do_run() -> None:
            try:
                ep = pipe.run_latest(dry_run=False)
                if ep is None:
                    asyncio.run_coroutine_threadsafe(
                        msg.reply_text("Playlist is empty — nothing to run."),
                        loop,
                    )
            except Exception as e:
                log.exception("/run failed: %s", e)
                asyncio.run_coroutine_threadsafe(
                    msg.reply_text(f"/run failed: {e}"),
                    loop,
                )

        loop = asyncio.get_event_loop()
        await asyncio.to_thread(_do_run)

    app = Application.builder().token(s.telegram_bot_token).build()
    app.add_handler(CommandHandler("run", on_run_command))
    app.add_handler(MessageHandler(filters.VOICE, on_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    log.info("Telegram bot polling started (text + voice + /run).")
    app.run_polling(allowed_updates=Update.ALL_TYPES)
