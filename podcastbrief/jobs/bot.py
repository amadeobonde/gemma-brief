from __future__ import annotations
import logging
from podcastbrief.core.config import load_settings
from podcastbrief.adapters.ollama_gemma import OllamaGemma
from podcastbrief.adapters.telegram_notifier import TelegramNotifier
from podcastbrief.bot.rag import RagBot

log = logging.getLogger(__name__)


def run_bot() -> None:
    """Run the Telegram RAG bot via long-poll.

    Uses python-telegram-bot's high-level Application for the polling loop and
    delegates to RagBot for answer generation.
    """
    s = load_settings()
    logging.basicConfig(level=getattr(logging, s.log_level.upper(), logging.INFO))

    if not s.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")

    llm = OllamaGemma(host=s.ollama_host, model=s.llm_model)
    rag = RagBot(llm=llm, notes_dir=s.notes_dir)

    from telegram import Update
    from telegram.ext import Application, MessageHandler, filters, ContextTypes

    async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.message.text:
            return
        user_id = str(update.message.from_user.id) if update.message.from_user else "anon"
        question = update.message.text.strip()
        log.info("RAG question from %s: %s", user_id, question[:80])
        try:
            answer = rag.answer(user_id=user_id, question=question)
        except Exception as e:
            log.exception("RAG answer failed: %s", e)
            answer = "Hit an issue. Try again in a moment."
        await update.message.reply_text(answer)

    app = Application.builder().token(s.telegram_bot_token).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    log.info("Telegram bot polling started.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)
