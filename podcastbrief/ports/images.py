from __future__ import annotations
from typing import Protocol
from podcastbrief.core.models import Episode


class ImageProvider(Protocol):
    def artwork(self, episode: Episode) -> bytes | None: ...
