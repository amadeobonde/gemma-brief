from __future__ import annotations
import io
import logging
import httpx

log = logging.getLogger(__name__)


class TelegramNotifier:
    """Thin Telegram Bot API wrapper for text + document send.

    Avoids python-telegram-bot's async surface for this synchronous use.
    """

    def __init__(self, *, bot_token: str) -> None:
        self.base = f"https://api.telegram.org/bot{bot_token}"

    def send_text(self, *, chat_id: str, text: str) -> None:
        r = httpx.post(
            f"{self.base}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=30,
        )
        if r.status_code >= 400:
            log.warning("Telegram sendMessage failed: %s %s", r.status_code, r.text)
            r.raise_for_status()

    def send_document(
        self,
        *,
        chat_id: str,
        data: bytes,
        filename: str,
        caption: str | None = None,
    ) -> None:
        files = {"document": (filename, io.BytesIO(data), "application/pdf")}
        form = {"chat_id": chat_id}
        if caption:
            form["caption"] = caption[:1024]
        r = httpx.post(
            f"{self.base}/sendDocument",
            data=form,
            files=files,
            timeout=120,
        )
        if r.status_code >= 400:
            log.warning("Telegram sendDocument failed: %s %s", r.status_code, r.text)
            r.raise_for_status()
