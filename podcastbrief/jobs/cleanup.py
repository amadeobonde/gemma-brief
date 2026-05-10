from __future__ import annotations
import logging
from podcastbrief.core.config import load_settings
from podcastbrief.adapters.fs_notes import FilesystemNoteStore
from podcastbrief.adapters.telegram_notifier import TelegramNotifier

log = logging.getLogger(__name__)


def run_cleanup() -> int:
    s = load_settings()
    logging.basicConfig(level=getattr(logging, s.log_level.upper(), logging.INFO))
    store = FilesystemNoteStore(base_dir=s.notes_dir)
    n = store.clear()
    notifier = TelegramNotifier(bot_token=s.telegram_bot_token)
    msg = f"Monthly podcast notes cleanup complete. Deleted {n} notes."
    for chat_id in s.chat_id_list:
        try:
            notifier.send_text(chat_id=chat_id, text=msg)
        except Exception as e:
            log.warning("Cleanup notify chat %s failed: %s", chat_id, e)
    return n
