"""Vault maintenance commands: backfill Whisper sidecars and original audio.

These idempotent CLI jobs let users upgrade an existing vault to support the
/debate clip-stitching feature (which needs both word-level Whisper output and
original audio retained on disk).
"""
from __future__ import annotations
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

import frontmatter

from podcastbrief.adapters.itunes_rss_feed import ItunesRssFeed, download_audio
from podcastbrief.adapters.whisper_http import WhisperHttpTranscriber
from podcastbrief.bot.index import INDEX_FILENAME
from podcastbrief.core.config import Settings
from podcastbrief.core.models import Episode
from podcastbrief.core.vault import (
    find_existing_audio,
    read_whisper_sidecar,
    store_audio,
    write_whisper_sidecar,
)

log = logging.getLogger(__name__)


def _iter_vault_notes(notes_dir: Path):
    """Yield (file_stem, metadata, body) for every brief in the vault."""
    for path in sorted(notes_dir.glob("*.md")):
        if path.name == INDEX_FILENAME:
            continue
        try:
            post = frontmatter.load(str(path))
        except Exception as e:
            log.warning("Skip unreadable note %s: %s", path.name, e)
            continue
        yield path.stem, dict(post.metadata or {}), post.content or ""


def _episode_id_from_meta(meta: dict) -> str:
    """Get episode_id from frontmatter, backfilling from legacy Spotify URLs."""
    eid = str(meta.get("episode_id") or "").strip()
    if eid:
        return eid
    spotify_url = str(meta.get("spotify") or "")
    if "/episode/" in spotify_url:
        return spotify_url.rstrip("/").rsplit("/episode/", 1)[-1].split("?")[0]
    return ""


def _episode_from_meta(meta: dict) -> Episode | None:
    """Reconstruct an Episode from vault frontmatter (best effort).

    Used for re-downloading audio. Spotify URL → Spotify episode id. YouTube/RSS
    episodes carry their source URL on `audio_url` in the original Episode model
    but the vault doesn't persist that; we fall back to the iTunes/RSS resolver
    for those when needed.
    """
    episode_id = _episode_id_from_meta(meta)
    if not episode_id:
        return None
    title = str(meta.get("title") or "")
    show = str(meta.get("show") or "")
    if not title or not show:
        return None
    return Episode(
        episode_id=episode_id,
        name=title,
        show_name=show,
        added_at=datetime.now(timezone.utc),
        duration_ms=0,
        spotify_url=str(meta.get("spotify") or ""),
        audio_url="",
    )


def _source_kind(meta: dict) -> str:
    """Identify which source originally produced an episode.

    Returns one of: 'youtube', 'rss', 'apple', 'spotify', 'unknown'.
    """
    eid = str(meta.get("episode_id") or "")
    if eid.startswith("yt-"):
        return "youtube"
    if eid.startswith("rss-"):
        return "rss"
    if eid.startswith("apple-"):
        return "apple"
    spotify = str(meta.get("spotify") or "")
    if "open.spotify.com/episode/" in spotify:
        return "spotify"
    return "unknown"


def redownload_audio(s: Settings) -> dict:
    """Backfill missing original audio for every episode in the vault.

    Returns a {stem: status} dict for reporting.
    """
    results: dict[str, str] = {}
    s.audio_store_path.mkdir(parents=True, exist_ok=True)

    itunes = ItunesRssFeed()
    yt_resolver = None
    yt_downloader = None

    for stem, meta, _body in _iter_vault_notes(s.notes_dir):
        episode_id = _episode_id_from_meta(meta)
        if not episode_id:
            results[stem] = "skip: no episode_id"
            continue

        existing = find_existing_audio(s.audio_store_path, episode_id)
        if existing:
            results[stem] = f"have: {existing.name}"
            continue

        ep = _episode_from_meta(meta)
        if ep is None:
            results[stem] = "skip: incomplete metadata"
            continue

        kind = _source_kind(meta)
        try:
            if kind == "spotify":
                ref = itunes.find_audio(ep)
                audio_bytes = download_audio(ref)
            elif kind == "youtube":
                if yt_resolver is None:
                    from podcastbrief.adapters.youtube_feed import (
                        YouTubeFeedResolver,
                        youtube_download_audio,
                    )
                    yt_resolver = YouTubeFeedResolver()
                    yt_downloader = youtube_download_audio
                # YouTube episode_id is `yt-{video_id}` — reconstruct watch URL.
                video_id = episode_id.removeprefix("yt-")
                ep.audio_url = f"https://www.youtube.com/watch?v={video_id}"
                ref = yt_resolver.find_audio(ep)
                audio_bytes = yt_downloader(ref)
            else:
                # RSS/Apple/unknown — try the iTunes fallback by show + title.
                # Last-ditch: works for many podcasts even without the original feed.
                ref = itunes.find_audio(ep)
                audio_bytes = download_audio(ref)
            path = store_audio(s.audio_store_path, episode_id, audio_bytes)
            results[stem] = f"downloaded: {path.name} ({len(audio_bytes)} bytes)"
        except Exception as e:
            log.warning("redownload failed for %s: %s", stem, e)
            results[stem] = f"fail: {e}"

    return results


def reindex_timestamps(s: Settings) -> dict:
    """Regenerate Whisper sidecars with word-level timestamps for every note.

    Idempotent — sidecars already containing word timestamps are skipped.
    Requires the original audio to be present in the audio store; run
    `podcastbrief redownload-audio` first if needed.
    """
    transcriber = WhisperHttpTranscriber(
        base_url=s.whisper_url, timeout_seconds=s.whisper_timeout_seconds
    )
    results: dict[str, str] = {}
    s.notes_dir.mkdir(parents=True, exist_ok=True)

    for stem, meta, _body in _iter_vault_notes(s.notes_dir):
        episode_id = _episode_id_from_meta(meta)
        if not episode_id:
            results[stem] = "skip: no episode_id"
            continue

        existing_sidecar = read_whisper_sidecar(s.notes_dir, stem)
        has_words = bool(existing_sidecar) and any(
            seg.get("words") for seg in (existing_sidecar.get("segments") or [])
        )
        if has_words:
            results[stem] = "skip: word-level sidecar already present"
            continue

        audio_path = find_existing_audio(s.audio_store_path, episode_id)
        if not audio_path:
            results[stem] = "fail: audio missing (run redownload-audio)"
            continue

        try:
            audio_bytes = audio_path.read_bytes()
            transcript = transcriber.transcribe(
                audio_bytes, filename=audio_path.name, force=True,
            )
            write_whisper_sidecar(
                notes_dir=s.notes_dir,
                file_stem=stem,
                transcript=transcript,
                episode_id=episode_id,
                audio_path=audio_path,
            )
            results[stem] = f"reindexed ({len(transcript.segments)} segments)"
        except Exception as e:
            log.warning("reindex failed for %s: %s", stem, e)
            results[stem] = f"fail: {e}"

    return results


def format_results(results: dict[str, str]) -> str:
    if not results:
        return "Vault is empty — nothing to do."
    lines = []
    for stem, status in results.items():
        lines.append(f"  {stem}: {status}")
    return "\n".join(lines)
