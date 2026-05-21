from __future__ import annotations
import hashlib
import json
import logging
from dataclasses import asdict
from pathlib import Path
import httpx
from podcastbrief.core.models import Transcript, TranscriptSegment, Word

log = logging.getLogger(__name__)


class WhisperHttpTranscriber:
    """Posts audio to a faster-whisper OpenAI-compatible HTTP endpoint.

    Uses `response_format=verbose_json` + `timestamp_granularities[]=word` so we
    receive both segment-level and word-level timestamps. Word timestamps are
    required by the /debate clip-extraction feature to trim clips to sentence
    boundaries without cutting words.

    Caches transcripts on disk by audio-bytes SHA-256 so repeated runs over the
    same episode skip the (slow) transcription step entirely. Old caches missing
    word timestamps are upgraded automatically on next transcription call with
    `force=True`.
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

    def transcribe(
        self,
        audio: bytes,
        *,
        filename: str = "audio.mp3",
        force: bool = False,
    ) -> Transcript:
        digest = hashlib.sha256(audio).hexdigest()
        cache_path = self.cache_dir / f"{digest}.json"
        if cache_path.exists() and not force:
            log.info("Whisper: cache hit for %s (%s)", filename, digest[:12])
            return self._load_cache(cache_path)
        if cache_path.exists() and force:
            log.info("Whisper: cache bypassed (force=True) for %s", filename)

        url = f"{self.base_url}/v1/audio/transcriptions"
        files = {"file": (filename, audio, "audio/mpeg")}
        # `timestamp_granularities[]` is the OpenAI-compatible param for word
        # timestamps; faster-whisper-server forwards it to faster-whisper's
        # `word_timestamps=True`. httpx's multipart `data` needs a dict with a
        # list value for repeated keys — a list of (k,v) tuples raises
        # TypeError when the multipart encoder tries to join values.
        data = {
            "response_format": "verbose_json",
            "timestamp_granularities[]": ["word", "segment"],
        }
        log.info("Whisper: transcribing %d bytes -> %s (word timestamps)", len(audio), url)
        resp = httpx.post(url, files=files, data=data, timeout=self.timeout_seconds)

        # Some Whisper builds 500 on word-timestamp requests (OOM, model
        # limitation, long audio). Fall back to segment-only — the brief still
        # works; /debate clip precision is reduced but nothing crashes.
        if resp.status_code >= 500:
            log.warning(
                "Whisper: word-timestamp request returned %d — retrying without "
                "word timestamps (segment-only). /debate clip precision reduced.",
                resp.status_code,
            )
            files = {"file": (filename, audio, "audio/mpeg")}  # rewind after read
            data_fallback = {"response_format": "verbose_json"}
            resp = httpx.post(url, files=files, data=data_fallback, timeout=self.timeout_seconds)

        resp.raise_for_status()
        body = resp.json()
        transcript = _parse_response(body)
        try:
            self._save_cache(cache_path, transcript)
        except Exception as e:
            log.warning("Transcript cache write failed: %s", e)
        return transcript

    @staticmethod
    def _save_cache(path: Path, t: Transcript) -> None:
        path.write_text(
            json.dumps(_transcript_to_dict(t), ensure_ascii=False),
            encoding="utf-8",
        )

    @staticmethod
    def _load_cache(path: Path) -> Transcript:
        data = json.loads(path.read_text(encoding="utf-8"))
        return _transcript_from_dict(data)


def _parse_response(body: dict) -> Transcript:
    """Build a Transcript from a faster-whisper-server verbose_json response.

    The server returns word timestamps either nested under each segment
    (`segment["words"]`) or as a flat top-level `words` array — we handle both.
    """
    flat_words_raw = body.get("words") or []
    flat_words = [
        Word(
            start=float(w.get("start", 0.0)),
            end=float(w.get("end", 0.0)),
            word=str(w.get("word", "")),
        )
        for w in flat_words_raw
    ]
    flat_idx = 0

    segments: list[TranscriptSegment] = []
    for s in body.get("segments", []):
        s_start = float(s.get("start", 0.0))
        s_end = float(s.get("end", 0.0))
        seg_words_raw = s.get("words") or []
        if seg_words_raw:
            seg_words = [
                Word(
                    start=float(w.get("start", 0.0)),
                    end=float(w.get("end", 0.0)),
                    word=str(w.get("word", "")),
                )
                for w in seg_words_raw
            ]
        else:
            # Pull from the flat top-level list, advancing the cursor.
            seg_words = []
            while flat_idx < len(flat_words) and flat_words[flat_idx].start < s_end + 0.01:
                if flat_words[flat_idx].start >= s_start - 0.01:
                    seg_words.append(flat_words[flat_idx])
                flat_idx += 1
        segments.append(
            TranscriptSegment(
                start=s_start,
                end=s_end,
                text=str(s.get("text", "")),
                words=seg_words,
            )
        )

    return Transcript(
        text=body.get("text", ""),
        segments=segments,
        language=body.get("language"),
        duration=_parse_duration(body),
    )


def _parse_duration(body: dict) -> float | None:
    for key in ("duration", "audio_duration", "total_duration"):
        if key in body and body[key] is not None:
            try:
                return float(body[key])
            except (TypeError, ValueError):
                continue
    return None


def _transcript_to_dict(t: Transcript) -> dict:
    return {
        "text": t.text,
        "language": t.language,
        "duration": t.duration,
        "segments": [
            {
                "start": s.start,
                "end": s.end,
                "text": s.text,
                "words": [asdict(w) for w in s.words],
            }
            for s in t.segments
        ],
    }


def _transcript_from_dict(data: dict) -> Transcript:
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
        text=data.get("text", ""),
        segments=segments,
        language=data.get("language"),
        duration=data.get("duration"),
    )
