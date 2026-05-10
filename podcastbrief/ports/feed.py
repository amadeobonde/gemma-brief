from __future__ import annotations
from typing import Protocol
from podcastbrief.core.models import Episode, AudioRef


class FeedResolver(Protocol):
    def find_audio(self, episode: Episode) -> AudioRef: ...
