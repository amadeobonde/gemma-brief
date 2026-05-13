"""Helpers for paths/sidecars co-located with vault notes.

The vault is the source of truth. Each episode has up to three artifacts:

  podcast_notes/
    {file_stem}.md                 ← markdown brief (with frontmatter)
    {file_stem}_whisper.json       ← Whisper sidecar (segments + words)
    audio_store/{episode_id}.{ext} ← original audio (mp3/m4a/etc.)

Sidecars are keyed by `file_stem` (date-prefixed safe title) so they sort
alongside their markdown. Audio is keyed by `episode_id` (immutable) so the
same file is reused across reprocessing runs.
"""
from __future__ import annotations
import json
import logging
import re
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from podcastbrief.core.models import Transcript, TranscriptSegment, Word

log = logging.getLogger(__name__)


SIDECAR_SUFFIX = "_whisper.json"


def sidecar_path(notes_dir: Path, file_stem: str) -> Path:
    return Path(notes_dir) / f"{file_stem}{SIDECAR_SUFFIX}"


def write_whisper_sidecar(
    *,
    notes_dir: Path,
    file_stem: str,
    transcript: Transcript,
    episode_id: str | None = None,
    audio_path: Path | None = None,
) -> Path:
    """Persist the full Whisper output as a JSON sidecar next to the brief."""
    notes_dir = Path(notes_dir)
    notes_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "episode_id": episode_id or "",
        "audio_path": str(audio_path) if audio_path else "",
        "language": transcript.language,
        "duration": transcript.duration,
        "segments": [
            {
                "start": s.start,
                "end": s.end,
                "text": s.text,
                "words": [asdict(w) for w in s.words],
            }
            for s in transcript.segments
        ],
    }
    path = sidecar_path(notes_dir, file_stem)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    log.info("Wrote Whisper sidecar: %s (%d segments)", path.name, len(transcript.segments))
    return path


def read_whisper_sidecar(notes_dir: Path, file_stem: str) -> dict | None:
    p = sidecar_path(notes_dir, file_stem)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("Failed to read sidecar %s: %s", p.name, e)
        return None


def sidecar_to_transcript(data: dict) -> Transcript:
    segments = []
    for s in data.get("segments", []):
        words = [
            Word(
                start=float(w.get("start", 0.0)),
                end=float(w.get("end", 0.0)),
                word=str(w.get("word", "")),
            )
            for w in s.get("words", []) or []
        ]
        segments.append(
            TranscriptSegment(
                start=float(s.get("start", 0.0)),
                end=float(s.get("end", 0.0)),
                text=str(s.get("text", "")),
                words=words,
            )
        )
    return Transcript(
        text="\n".join(s.text for s in segments),
        segments=segments,
        language=data.get("language"),
        duration=data.get("duration"),
    )


# ---------------------- audio store ----------------------


_AUDIO_EXT_FOR_MIME = {
    b"ID3": "mp3",        # MP3 with ID3 tag
    b"\xff\xfb": "mp3",   # MPEG audio frame sync
    b"\xff\xf3": "mp3",
    b"\xff\xf2": "mp3",
    b"OggS": "ogg",
    b"RIFF": "wav",
}


def detect_audio_ext(audio: bytes) -> str:
    head = audio[:4]
    for sig, ext in _AUDIO_EXT_FOR_MIME.items():
        if head.startswith(sig):
            return ext
    # ftypM4A or ftypmp42 starts at byte 4
    if len(audio) >= 12 and audio[4:8] == b"ftyp":
        return "m4a"
    return "mp3"


def audio_store_path_for(audio_store_dir: Path, episode_id: str, audio_bytes: bytes | None = None) -> Path:
    """Where the original audio for `episode_id` lives in the audio store.

    Slugifies the id (so YouTube ids with `-` are safe). If `audio_bytes` is
    provided, picks the extension from the magic bytes; otherwise defaults to
    mp3 (a `find_existing_audio` helper finds the actual extension on disk).
    """
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", episode_id)[:120] or "episode"
    ext = detect_audio_ext(audio_bytes) if audio_bytes else "mp3"
    return Path(audio_store_dir) / f"{safe}.{ext}"


def find_existing_audio(audio_store_dir: Path, episode_id: str) -> Path | None:
    """Look up an already-stored audio file regardless of extension."""
    audio_store_dir = Path(audio_store_dir)
    if not audio_store_dir.exists():
        return None
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", episode_id)[:120] or "episode"
    for ext in ("mp3", "m4a", "ogg", "wav", "opus"):
        p = audio_store_dir / f"{safe}.{ext}"
        if p.exists():
            return p
    return None


def store_audio(audio_store_dir: Path, episode_id: str, audio_bytes: bytes) -> Path:
    """Persist downloaded audio bytes to the audio store, returning the path."""
    audio_store_dir = Path(audio_store_dir)
    audio_store_dir.mkdir(parents=True, exist_ok=True)
    path = audio_store_path_for(audio_store_dir, episode_id, audio_bytes=audio_bytes)
    path.write_bytes(audio_bytes)
    log.info("Stored audio: %s (%d bytes)", path.name, len(audio_bytes))
    return path
