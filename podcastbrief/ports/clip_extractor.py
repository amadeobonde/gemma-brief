"""Port for audio-clip extraction and stitching (used by /debate)."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence


@dataclass
class ClipMetadata:
    """A normalized, ready-to-stitch audio clip."""
    audio_path: Path           # Path to a temp file holding the trimmed + normalized clip
    label: str                 # Speaker label or host name (used in voice-note caption)
    duration_seconds: float


class ClipExtractor(Protocol):
    """Cut, normalize, and stitch audio clips for the /debate voice note."""

    def extract_clip(
        self,
        audio_path: Path,
        start_seconds: float,
        end_seconds: float,
        padding_seconds: float = 0.75,
    ) -> Path:
        """Extract a clip from an audio file with padding. Returns temp file path."""
        ...

    def normalize_volume(self, clip_path: Path, target_dbfs: float = -18.0) -> Path:
        """Normalize clip volume to target dBFS. Returns normalized temp file path."""
        ...

    def stitch_clips(
        self,
        clips: Sequence[ClipMetadata],
        silence_between_ms: int = 800,
    ) -> Path:
        """Stitch multiple clips with silence between them. Returns final OGG file path."""
        ...
