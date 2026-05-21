"""Orchestrates enricher adapters in parallel and caches results.

Called by Pipeline._process_episode after Pass 2 (interrogator) completes and
before PDF rendering. Each adapter has its own timeout and graceful fallback;
asyncio.gather with return_exceptions=True ensures one adapter failing does
not nuke the others.

Cache: a JSON sidecar at {notes_dir}/.enrichment/{episode_id}.json. Re-runs of
the same episode (e.g. /run) read the cache instead of re-fetching, unless
force=True bypasses it.
"""
from __future__ import annotations
import asyncio
import json
import logging
from dataclasses import asdict
from pathlib import Path

from podcastbrief.ports.enricher import (
    Enricher,
    EnrichmentResult,
    NewsArticle,
    WikiEntity,
)

log = logging.getLogger(__name__)


def _serialize(r: EnrichmentResult) -> dict:
    return {
        "wiki": [asdict(w) for w in r.wiki],
        "news": [asdict(n) for n in r.news],
    }


def _deserialize(data: dict) -> EnrichmentResult:
    return EnrichmentResult(
        wiki=[
            WikiEntity(
                name=w["name"],
                summary=w["summary"],
                url=w["url"],
                thumbnail_url=w.get("thumbnail_url"),
            )
            for w in data.get("wiki") or []
        ],
        news=[
            NewsArticle(
                title=n["title"],
                source=n["source"],
                pub_date=n["pub_date"],
                url=n["url"],
                summary=n["summary"],
                score=n.get("score", 0.0),
                annotation=n.get("annotation", ""),
            )
            for n in data.get("news") or []
        ],
    )


def _cache_path(notes_dir: Path, episode_id: str) -> Path:
    d = notes_dir / ".enrichment"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{episode_id}.json"


async def run_enrichers_async(
    enrichers: list[Enricher],
    *,
    named_entities: list[str],
    episode_pub_date: str | None,
    accent_hex: str,
) -> EnrichmentResult:
    if not enrichers:
        return EnrichmentResult()
    tasks = [
        e.enrich(
            named_entities=named_entities,
            episode_pub_date=episode_pub_date,
            accent_hex=accent_hex,
        )
        for e in enrichers
    ]
    parts: list = await asyncio.gather(*tasks, return_exceptions=True)
    merged = EnrichmentResult()
    for p in parts:
        if isinstance(p, Exception):
            log.warning("Enricher failed: %s", p)
            continue
        merged.wiki.extend(p.wiki)
        merged.news.extend(p.news)
    return merged


def run_enrichers(
    enrichers: list[Enricher],
    *,
    notes_dir: Path,
    episode_id: str,
    named_entities: list[str],
    episode_pub_date: str | None,
    accent_hex: str,
    force: bool = False,
) -> EnrichmentResult:
    """Sync entrypoint for the pipeline. Reads cache unless force=True."""
    cache = _cache_path(notes_dir, episode_id)
    if cache.exists() and not force:
        try:
            log.info("Enrichment: cache hit %s", cache.name)
            return _deserialize(json.loads(cache.read_text(encoding="utf-8")))
        except Exception as e:
            log.warning("Enrichment cache read failed (%s); refetching", e)
    result = asyncio.run(
        run_enrichers_async(
            enrichers,
            named_entities=named_entities,
            episode_pub_date=episode_pub_date,
            accent_hex=accent_hex,
        )
    )
    try:
        cache.write_text(json.dumps(_serialize(result)), encoding="utf-8")
    except Exception as e:
        log.warning("Enrichment cache write failed: %s", e)
    return result


def write_annotations_to_cache(
    notes_dir: Path, episode_id: str, result: EnrichmentResult
) -> None:
    """Re-serialize after Pass 3 fills in `annotation` fields so the next /run
    can read the annotated cache rather than re-prompting Gemma."""
    cache = _cache_path(notes_dir, episode_id)
    try:
        cache.write_text(json.dumps(_serialize(result)), encoding="utf-8")
    except Exception as e:
        log.warning("Enrichment cache rewrite failed: %s", e)
