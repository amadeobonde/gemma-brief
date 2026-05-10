from __future__ import annotations
import hashlib
import json
import logging
from dataclasses import asdict
from pathlib import Path
import httpx
from podcastbrief.core.models import Transcript, TranscriptSegment

log = logging.getLogger(__name__)


class WhisperHttpTranscriber:
    """Posts audio to a faster-whisper OpenAI-compatible HTTP endpoint.

    Switched from the n8n version's default JSON to `verbose_json` so we get
    segment-level timestamps for quote attribution.

    Caches transcripts on disk by audio-bytes SHA-256 so repeated runs over the
    same episode skip the (slow) transcription step entirely.
    """

    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: int = 1800,
        cache_dir: Path | str = Path(".transcript_cache"),
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def transcribe(self, audio: bytes, *, filename: str = "audio.mp3") -> Transcript:
        digest = hashlib.sha256(audio).hexdigest()
        cache_path = self.cache_dir / f"{digest}.json"
        if cache_path.exists():
            log.info("Whisper: cache hit for %s (%s)", filename, digest[:12])
            return self._load_cache(cache_path)

        url = f"{self.base_url}/v1/audio/transcriptions"
        files = {"file": (filename, audio, "audio/mpeg")}
        data = {"response_format": "verbose_json"}
        log.info("Whisper: transcribing %d bytes -> %s", len(audio), url)
        resp = httpx.post(url, files=files, data=data, timeout=self.timeout_seconds)
        resp.raise_for_status()
        body = resp.json()
        segments = [
            TranscriptSegment(
                start=float(s.get("start", 0.0)),
                end=float(s.get("end", 0.0)),
                text=str(s.get("text", "")),
            )
            for s in body.get("segments", [])
        ]
        transcript = Transcript(
            text=body.get("text", ""),
            segments=segments,
            language=body.get("language"),
        )
        try:
            self._save_cache(cache_path, transcript)
        except Exception as e:
            log.warning("Transcript cache write failed: %s", e)
        return transcript

    @staticmethod
    def _save_cache(path: Path, t: Transcript) -> None:
        path.write_text(
            json.dumps(
                {
                    "text": t.text,
                    "segments": [asdict(s) for s in t.segments],
                    "language": t.language,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    @staticmethod
    def _load_cache(path: Path) -> Transcript:
        data = json.loads(path.read_text(encoding="utf-8"))
        segments = [
            TranscriptSegment(
                start=float(s.get("start", 0.0)),
                end=float(s.get("end", 0.0)),
                text=str(s.get("text", "")),
            )
            for s in data.get("segments", [])
        ]
        return Transcript(
            text=data.get("text", ""),
            segments=segments,
            language=data.get("language"),
        )
