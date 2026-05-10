from __future__ import annotations
from typing import Protocol
from podcastbrief.core.models import Transcript


class Transcriber(Protocol):
    def transcribe(self, audio: bytes, *, filename: str = "audio.mp3") -> Transcript: ...
