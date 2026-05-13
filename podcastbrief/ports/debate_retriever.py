"""Port + data model for cross-episode debate clip retrieval."""
from __future__ import annotations
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, Field, field_validator


Stance = Literal["bullish", "bearish", "neutral", "uncertain"]


class TopicMoment(BaseModel):
    """One audio moment from one episode where a host weighed in on a topic.

    Returned by `DebateRetriever.find_topic_moments` and consumed by both the
    audio stitcher (/debate) and the text-only /moments command.
    """

    episode_slug: str           # vault file_stem
    episode_title: str
    host_name: str              # extracted from episode metadata (show name fallback)
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    transcript_text: str
    relevance_score: float = 0.0
    stance: Stance = "neutral"

    @field_validator("end_seconds")
    @classmethod
    def _end_after_start(cls, v: float, info) -> float:
        start = info.data.get("start_seconds", 0.0)
        if v <= start:
            raise ValueError(f"end_seconds ({v}) must exceed start_seconds ({start})")
        return v

    @property
    def duration_seconds(self) -> float:
        return self.end_seconds - self.start_seconds


class DebateRetriever(Protocol):
    def find_topic_moments(
        self,
        topic: str,
        vault_path: Path,
        max_clips: int = 4,
    ) -> list[TopicMoment]:
        """Find the most relevant and contrasting moments across vault episodes for a topic."""
        ...
