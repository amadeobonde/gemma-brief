"""/explain command: pull the full verbatim discussion of any keyword or topic.

Usage: /explain google spark

Searches Whisper sidecars across the vault using BM25, finds the best-matching
segment, then expands the window to capture the full surrounding passage (all
consecutive segments with no gap > 15 s, up to 2 minutes). Returns:

  1. An OGG voice note of the passage (if the original audio is on disk).
  2. The verbatim transcript text with episode title + timestamp.

If audio is unavailable (not yet downloaded, ffmpeg missing) the voice note is
skipped and only the transcript text is returned.
"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from pathlib import Path

from podcastbrief.adapters.gemma_debate_retriever import (
    _Candidate,
    _collect_segments,
    _bm25_rank,
)
from podcastbrief.adapters.pydub_clip_extractor import PydubClipExtractor, ffmpeg_available
from podcastbrief.core.vault import read_whisper_sidecar

log = logging.getLogger(__name__)


# ─── result ───────────────────────────────────────────────────────────────────


@dataclass
class ExplainResult:
    """Return value from run_explain."""
    ogg_bytes: bytes | None
    transcript_text: str
    episode_slug: str
    episode_title: str
    start_seconds: float
    end_seconds: float
    error: str | None = None

    @classmethod
    def err(cls, msg: str) -> "ExplainResult":
        return cls(
            ogg_bytes=None,
            transcript_text="",
            episode_slug="",
            episode_title="",
            start_seconds=0.0,
            end_seconds=0.0,
            error=msg,
        )


# ─── passage expansion ────────────────────────────────────────────────────────


def _expand_passage(
    best: _Candidate,
    sidecar: dict,
    *,
    max_gap_seconds: float = 15.0,
    max_duration_seconds: float = 120.0,
) -> tuple[float, float, str]:
    """Expand from `best` outward to capture the full surrounding discussion.

    Walks backwards and forwards through the sidecar's segment list, including
    each neighbour as long as:
      - the gap to the previous/next segment is ≤ max_gap_seconds, AND
      - accumulated duration is ≤ max_duration_seconds.

    Returns (start_seconds, end_seconds, verbatim_text).
    """
    segments = sidecar.get("segments") or []
    if not segments:
        return best.start_seconds, best.end_seconds, best.text

    idx = best.segment_index
    # Guard against stale index (can happen if sidecar was rebuilt).
    idx = max(0, min(idx, len(segments) - 1))
    included: list[int] = [idx]

    # ── expand backwards ──
    for j in range(idx - 1, -1, -1):
        seg_end = float(segments[j].get("end") or 0.0)
        next_start = float(segments[j + 1].get("start") or 0.0)
        if next_start - seg_end > max_gap_seconds:
            break
        included.insert(0, j)
        total = (
            float(segments[included[-1]].get("end") or 0.0)
            - float(segments[included[0]].get("start") or 0.0)
        )
        if total >= max_duration_seconds:
            break

    # ── expand forwards ──
    for j in range(idx + 1, len(segments)):
        seg_start = float(segments[j].get("start") or 0.0)
        prev_end = float(segments[j - 1].get("end") or 0.0)
        if seg_start - prev_end > max_gap_seconds:
            break
        included.append(j)
        total = (
            float(segments[included[-1]].get("end") or 0.0)
            - float(segments[included[0]].get("start") or 0.0)
        )
        if total >= max_duration_seconds:
            break

    start = float(segments[included[0]].get("start") or 0.0)
    end = float(segments[included[-1]].get("end") or 0.0)
    # Ensure end > start (Whisper occasionally emits zero-length segments).
    end = max(end, start + 1.0)

    text = " ".join(
        (segments[j].get("text") or "").strip()
        for j in included
        if (segments[j].get("text") or "").strip()
    )
    return start, end, text


# ─── audio resolution ─────────────────────────────────────────────────────────


def _resolve_audio_path(episode_slug: str, notes_dir: Path, audio_store_dir: Path) -> Path | None:
    """Find the stored audio for one episode (mirrors debate_handler logic)."""
    import frontmatter
    from podcastbrief.bot.index import INDEX_FILENAME
    from podcastbrief.core.vault import find_existing_audio
    from podcastbrief.jobs.maintenance import _episode_id_from_meta

    md_path = notes_dir / f"{episode_slug}.md"
    episode_id = ""
    stored_path = ""
    if md_path.exists() and md_path.name != INDEX_FILENAME:
        try:
            post = frontmatter.load(str(md_path))
            meta = dict(post.metadata or {})
            episode_id = _episode_id_from_meta(meta)
            stored_path = str(meta.get("audio_path") or "")
        except Exception as e:
            log.warning("Couldn't read frontmatter for %s: %s", episode_slug, e)

    if stored_path:
        p = Path(stored_path)
        if p.exists():
            return p
    if episode_id:
        return find_existing_audio(audio_store_dir, episode_id)
    return None


# ─── main entry point ─────────────────────────────────────────────────────────


def run_explain(
    *,
    query: str,
    notes_dir: Path,
    audio_store_dir: Path,
    target_dbfs: float = -18.0,
    padding_seconds: float = 0.3,
) -> ExplainResult:
    """Find and return the best-matching passage for *query*.

    Safe to call from a worker thread (no async).
    """
    if not query.strip():
        return ExplainResult.err("Usage: /explain <keyword or topic>")

    # ── 1. BM25 search across all Whisper sidecars ──
    cands_all = _collect_segments(Path(notes_dir))
    if not cands_all:
        return ExplainResult.err(
            "No transcripts in the vault yet — process an episode first."
        )

    top = _bm25_rank(cands_all, query, top_k=5)
    if not top:
        return ExplainResult.err(
            f"Couldn't find '{query}' in any episode transcript. "
            "Try a different keyword, or check that the episode has been processed."
        )

    best = top[0]

    # ── 2. Expand to surrounding passage ──
    sidecar = read_whisper_sidecar(notes_dir, best.episode_slug) or {}
    start, end, passage_text = _expand_passage(best, sidecar)

    if not passage_text:
        passage_text = best.text  # last-resort fallback

    # ── 3. Extract audio clip (best-effort; skip if audio/ffmpeg unavailable) ──
    ogg_bytes: bytes | None = None
    if ffmpeg_available():
        audio_path = _resolve_audio_path(best.episode_slug, notes_dir, audio_store_dir)
        if audio_path:
            try:
                with PydubClipExtractor() as extractor:
                    raw = extractor.extract_clip(
                        audio_path=audio_path,
                        start_seconds=start,
                        end_seconds=end,
                        padding_seconds=padding_seconds,
                    )
                    normalized = extractor.normalize_volume(raw, target_dbfs=target_dbfs)
                    ogg_bytes = normalized.read_bytes()
            except Exception as e:
                log.warning("/explain audio extraction failed for %s: %s", best.episode_slug, e)

    return ExplainResult(
        ogg_bytes=ogg_bytes,
        transcript_text=passage_text,
        episode_slug=best.episode_slug,
        episode_title=best.episode_title,
        start_seconds=start,
        end_seconds=end,
    )
