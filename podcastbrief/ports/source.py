from __future__ import annotations
from typing import Protocol
from podcastbrief.core.models import Episode


class PodcastSource(Protocol):
    def list_recent_episodes(self, *, hours: int = 24) -> list[Episode]: ...
