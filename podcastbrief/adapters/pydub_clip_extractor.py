"""Pydub-backed implementation of ClipExtractor.

Used by the /debate command to build a single OGG voice note out of multiple
host clips drawn from different episodes. Requires `ffmpeg` on PATH (pydub
shells out to it for non-WAV formats).
"""
from __future__ import annotations
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Sequence

from podcastbrief.ports.clip_extractor import ClipMetadata

log = logging.getLogger(__name__)


def _require_pydub():
    try:
        from pydub import AudioSegment  # type: ignore
    except ImportError as e:  # pragma: no cover — runtime guard
        raise RuntimeError(
            "pydub is required for the /debate feature. Install with: "
            "pip install 'pydub>=0.25.1'  (also requires ffmpeg on PATH)"
        ) from e
    return AudioSegment


def ffmpeg_available() -> bool:
    """Pydub shells out to both `ffmpeg` (encode/decode) and `ffprobe` (metadata)."""
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def missing_ffmpeg_binaries() -> list[str]:
    """Return which of ffmpeg/ffprobe are missing on PATH (for error messages)."""
    return [name for name in ("ffmpeg", "ffprobe") if not shutil.which(name)]


class PydubClipExtractor:
    """Cut, normalize, and stitch audio clips with pydub.

    Each call returns a Path under a per-instance temp directory; call
    `cleanup()` (or use as a context manager) to wipe them when done.
    """

    def __init__(self, *, work_dir: Path | None = None) -> None:
        self._owns_dir = work_dir is None
        self._dir = Path(work_dir) if work_dir else Path(tempfile.mkdtemp(prefix="debate_clips_"))
        self._dir.mkdir(parents=True, exist_ok=True)
        missing = missing_ffmpeg_binaries()
        if missing:
            raise RuntimeError(
                f"Missing required binaries on PATH: {', '.join(missing)}. "
                "Install the full ffmpeg suite (includes ffprobe). "
                "macOS: `brew install ffmpeg`. Debian: `apt install ffmpeg`."
            )
        self._counter = 0

    def __enter__(self) -> "PydubClipExtractor":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.cleanup()

    def cleanup(self) -> None:
        if self._owns_dir:
            shutil.rmtree(self._dir, ignore_errors=True)

    def _next_tmp(self, suffix: str) -> Path:
        self._counter += 1
        return self._dir / f"clip_{self._counter:04d}{suffix}"

    def extract_clip(
        self,
        audio_path: Path,
        start_seconds: float,
        end_seconds: float,
        padding_seconds: float = 0.75,
    ) -> Path:
        AudioSegment = _require_pydub()
        if end_seconds <= start_seconds:
            raise ValueError(
                f"extract_clip: end ({end_seconds}) must be > start ({start_seconds})"
            )
        fmt = _infer_format(audio_path)
        log.info(
            "extract_clip %s [%.2f-%.2f] (+/- %.2fs pad) fmt=%s",
            audio_path.name, start_seconds, end_seconds, padding_seconds, fmt,
        )
        segment = AudioSegment.from_file(str(audio_path), format=fmt)
        total_ms = len(segment)
        start_ms = max(0, int((start_seconds - padding_seconds) * 1000))
        end_ms = min(total_ms, int((end_seconds + padding_seconds) * 1000))
        clip = segment[start_ms:end_ms]
        out = self._next_tmp(".wav")
        clip.export(str(out), format="wav")
        return out

    def normalize_volume(self, clip_path: Path, target_dbfs: float = -18.0) -> Path:
        AudioSegment = _require_pydub()
        seg = AudioSegment.from_file(str(clip_path))
        if seg.dBFS == float("-inf"):
            # Silent clip — nothing to normalize.
            out = self._next_tmp("_norm.wav")
            seg.export(str(out), format="wav")
            return out
        gain = target_dbfs - seg.dBFS
        # Clip extreme gains to avoid pumping noise floor when a clip is near-silent.
        gain = max(-12.0, min(12.0, gain))
        normalized = seg.apply_gain(gain)
        out = self._next_tmp("_norm.wav")
        normalized.export(str(out), format="wav")
        log.debug("normalize_volume %s dBFS=%.1f gain=%+.1f", clip_path.name, seg.dBFS, gain)
        return out

    def stitch_clips(
        self,
        clips: Sequence[ClipMetadata],
        silence_between_ms: int = 800,
    ) -> Path:
        if not clips:
            raise ValueError("stitch_clips: empty clip list")
        AudioSegment = _require_pydub()

        silence = AudioSegment.silent(duration=silence_between_ms, frame_rate=48000)
        combined: "AudioSegment | None" = None
        for clip in clips:
            seg = AudioSegment.from_file(str(clip.audio_path))
            # Force mono 48kHz to match the OGG/Opus target before concatenation
            # so pydub doesn't resample piecewise.
            seg = seg.set_channels(1).set_frame_rate(48000)
            if combined is None:
                combined = seg
            else:
                combined = combined + silence + seg

        out = self._next_tmp("_stitched.ogg")
        # Telegram voice-note format: OGG/Opus mono. libopus must be available
        # in the local ffmpeg build (it is in nearly every modern build).
        combined.export(
            str(out),
            format="ogg",
            codec="libopus",
            bitrate="64k",
            parameters=["-ac", "1", "-ar", "48000"],
        )
        log.info(
            "stitch_clips: wrote %s (%d clips, %dms gap)",
            out.name, len(clips), silence_between_ms,
        )
        return out


def _infer_format(path: Path) -> str:
    ext = path.suffix.lower().lstrip(".")
    return {"m4a": "mp4", "mp4a": "mp4"}.get(ext, ext or "mp3")
