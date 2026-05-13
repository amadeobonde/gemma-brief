"""/debate command orchestrator: retrieve → extract → stitch → analyze.

Holds the full audio-debate flow in one place so jobs/bot.py just wires it up.
Designed to run inside an `asyncio.to_thread` call from the Telegram handler.
"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from pathlib import Path

import frontmatter

from podcastbrief.adapters.gemma_debate_retriever import GemmaDebateRetriever
from podcastbrief.adapters.pydub_clip_extractor import (
    PydubClipExtractor,
    ffmpeg_available,
    missing_ffmpeg_binaries,
)
from podcastbrief.briefing.debate_analyzer import synthesize_debate_analysis
from podcastbrief.bot.index import INDEX_FILENAME
from podcastbrief.core.vault import find_existing_audio
from podcastbrief.ports.clip_extractor import ClipMetadata
from podcastbrief.ports.debate_retriever import TopicMoment
from podcastbrief.ports.llm import LLM

log = logging.getLogger(__name__)


@dataclass
class DebateResult:
    """One of two shapes — either a finished voice note + text, or just text."""
    ogg_bytes: bytes | None
    analysis_md: str
    sources_md: str
    error: str | None = None

    @classmethod
    def err(cls, msg: str) -> "DebateResult":
        return cls(ogg_bytes=None, analysis_md="", sources_md="", error=msg)


def run_debate(
    *,
    topic: str,
    llm: LLM,
    notes_dir: Path,
    audio_store_dir: Path,
    target_dbfs: float = -18.0,
    padding_seconds: float = 0.75,
    silence_between_ms: int = 800,
    max_clips: int = 4,
) -> DebateResult:
    """End-to-end debate compilation. Safe to call from a worker thread.

    Returns a `DebateResult` carrying the OGG voice note, the analysis text,
    and the source-episode list — or `error` set if we couldn't proceed.
    """
    if not topic.strip():
        return DebateResult.err("Usage: /debate <topic>")

    missing = missing_ffmpeg_binaries()
    if missing:
        return DebateResult.err(
            f"/debate requires the full ffmpeg suite — missing on PATH: {', '.join(missing)}. "
            "macOS: `brew install ffmpeg`. Debian: `apt install ffmpeg`."
        )

    retriever = GemmaDebateRetriever(llm=llm)
    try:
        moments = retriever.find_topic_moments(topic, notes_dir, max_clips=max_clips)
    except Exception as e:
        log.exception("/debate retrieval failed: %s", e)
        return DebateResult.err(f"Couldn't search the vault: {e}")

    if len(moments) < 2:
        return DebateResult.err(
            f"Not enough episodes cover '{topic}' yet. "
            "Add more podcasts on this subject and try again."
        )

    # Resolve each moment to a stored audio file.
    audio_paths = _resolve_audio_paths(moments, notes_dir, audio_store_dir)
    missing = [m.episode_slug for m, p in zip(moments, audio_paths) if p is None]
    if missing:
        return DebateResult.err(
            "Missing original audio for: "
            + ", ".join(missing)
            + ". Run `podcastbrief redownload-audio` to backfill, then retry /debate."
        )

    # Extract + normalize + stitch.
    try:
        with PydubClipExtractor() as extractor:
            clips: list[ClipMetadata] = []
            for moment, audio_path in zip(moments, audio_paths):
                raw_clip = extractor.extract_clip(
                    audio_path=audio_path,
                    start_seconds=moment.start_seconds,
                    end_seconds=moment.end_seconds,
                    padding_seconds=padding_seconds,
                )
                normalized = extractor.normalize_volume(raw_clip, target_dbfs=target_dbfs)
                clips.append(ClipMetadata(
                    audio_path=normalized,
                    label=moment.host_name,
                    duration_seconds=moment.duration_seconds,
                ))
            stitched = extractor.stitch_clips(clips, silence_between_ms=silence_between_ms)
            ogg_bytes = stitched.read_bytes()
    except Exception as e:
        log.exception("/debate stitching failed: %s", e)
        return DebateResult.err(f"Couldn't build the voice note: {e}")

    # Synthesize analysis text.
    analysis_md = synthesize_debate_analysis(llm=llm, topic=topic, moments=moments)
    sources_md = _format_sources(topic, moments)

    return DebateResult(
        ogg_bytes=ogg_bytes,
        analysis_md=analysis_md,
        sources_md=sources_md,
    )


def _resolve_audio_paths(
    moments: list[TopicMoment],
    notes_dir: Path,
    audio_store_dir: Path,
) -> list[Path | None]:
    """For each moment, locate the original audio file via the vault frontmatter.

    Handles both the new `episode_id` frontmatter and legacy notes that carry
    only a `spotify` URL — derives the id from the URL when needed.
    """
    from podcastbrief.jobs.maintenance import _episode_id_from_meta

    out: list[Path | None] = []
    for m in moments:
        md_path = notes_dir / f"{m.episode_slug}.md"
        episode_id = ""
        stored_path = ""
        if md_path.exists() and md_path.name != INDEX_FILENAME:
            try:
                post = frontmatter.load(str(md_path))
                meta = dict(post.metadata or {})
                episode_id = _episode_id_from_meta(meta)
                stored_path = str(meta.get("audio_path") or "")
            except Exception as e:
                log.warning("Couldn't read frontmatter for %s: %s", m.episode_slug, e)

        # Prefer the explicit stored path; fall back to the audio store lookup.
        if stored_path:
            p = Path(stored_path)
            if p.exists():
                out.append(p)
                continue
        if episode_id:
            p = find_existing_audio(audio_store_dir, episode_id)
            if p:
                out.append(p)
                continue
        out.append(None)
    return out


def _format_sources(topic: str, moments: list[TopicMoment]) -> str:
    lines = [f"📚 Source clips for '{topic}':"]
    for m in moments:
        start = _fmt_ts(m.start_seconds)
        end = _fmt_ts(m.end_seconds)
        lines.append(
            f"\n[[{m.episode_slug}]] — {m.host_name}\n"
            f"  · {start}–{end} ({m.stance})\n"
            f"  · {m.episode_title}"
        )
    return "\n".join(lines)


def _fmt_ts(seconds: float) -> str:
    s = max(0, int(seconds))
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"
