from __future__ import annotations
from typing import Protocol


class StoredNote(Protocol):
    file_stem: str
    body: str
    metadata: dict


class NoteStore(Protocol):
    def save(self, *, file_stem: str, body: str, metadata: dict) -> str: ...

    def list_recent(self, limit: int = 30) -> list[StoredNote]: ...

    def find_by_episode_id(self, episode_id: str) -> StoredNote | None: ...

    def clear(self) -> int: ...
