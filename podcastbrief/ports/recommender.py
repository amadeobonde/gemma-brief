from __future__ import annotations
from typing import Protocol
from podcastbrief.core.models import Suggestion


class Recommender(Protocol):
    def similar(self, *, query: str, limit: int = 5) -> list[Suggestion]: ...
