from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class Episode:
    episode_id: str
    name: str
    show_name: str
    added_at: datetime
    duration_ms: int
    spotify_url: str
    audio_url: str = ""  # Direct audio URL for YouTube/RSS sources; empty = use FeedResolver


@dataclass
class AudioRef:
    url: str
    title: str
    pub_date: str | None
    show_name: str


@dataclass
class Word:
    start: float
    end: float
    word: str


@dataclass
class TranscriptSegment:
    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)


@dataclass
class Transcript:
    text: str
    segments: list[TranscriptSegment] = field(default_factory=list)
    language: str | None = None
    duration: float | None = None

    def with_timestamps(self) -> str:
        if not self.segments:
            return self.text
        lines = []
        for s in self.segments:
            mm = int(s.start) // 60
            ss = int(s.start) % 60
            lines.append(f"[{mm:02d}:{ss:02d}] {s.text.strip()}")
        return "\n".join(lines)

    @property
    def has_word_timestamps(self) -> bool:
        return any(seg.words for seg in self.segments)


@dataclass
class Suggestion:
    title: str
    show: str
    url: str


@dataclass
class BriefArtifacts:
    pdf_bytes: bytes
    markdown: str
    file_stem: str
    pdf_path: Path | None = None
