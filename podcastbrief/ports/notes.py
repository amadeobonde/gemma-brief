from __future__ import annotations
from typing import Protocol


class StoredNote(Protocol):
    file_stem: str
    body: str
    metadata: dict


class NoteStore(Protocol):
    def save(self, *, file_stem: str, body: str, metadata: dict) -> str: ...

    def list_recent(self, limit: int = 30) -> list[StoredNote]: ...

    def clear(self) -> int: ...
