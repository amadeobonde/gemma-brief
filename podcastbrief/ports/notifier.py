from __future__ import annotations
from typing import Protocol


class Notifier(Protocol):
    def send_text(self, *, chat_id: str, text: str) -> None: ...

    def send_document(
        self,
        *,
        chat_id: str,
        data: bytes,
        filename: str,
        caption: str | None = None,
    ) -> None: ...
