"""Orchestrates the four enricher adapters in parallel and caches results.

Called by Pipeline._process_episode after Pass 2 (interrogator) completes and
before PDF rendering. Each adapter has its own timeout and graceful fallback;
asyncio.gather with return_exceptions=True ensures one adapter failing does
not nuke the others.

Cache: a JSON sidecar at {notes_dir}/.enrichment/{episode_id}.json. Re-runs of
the same episode (e.g. /run) read the cache instead of re-fetching, unless
force=True bypasses it. Binary chart PNGs are written to a parallel directory
so the JSON stays small and readable.
"""
from __future__ import annotations
import asyncio
import base64
import json
import logging
from dataclasses import asdict
from pathlib import Path

from podcastbrief.ports.enricher import (
    Enricher,
    EnrichmentResult,
    MacroSeries,
    MarketChart,
    NewsArticle,
    WikiEntity,
)

log = logging.getLogger(__name__)


def _b64_or_none(b: bytes | None) -> str | None:
    if b is None:
        return None
    return base64.b64encode(b).decode("ascii")


def _from_b64(s: str | None) -> bytes | None:
    if not s:
        return None
    return base64.b64decode(s)


def _serialize(r: EnrichmentResult) -> dict:
    def _market(m: MarketChart) -> dict:
        d = asdict(m)
        d["chart_png"] = _b64_or_none(m.chart_png)
        return d

    def _macro(m: MacroSeries) -> dict:
        d = asdict(m)
        d["chart_png"] = _b64_or_none(m.chart_png)
        return d

    return {
        "market": [_market(m) for m in r.market],
        "macro": [_macro(m) for m in r.macro],
        "wiki": [asdict(w) for w in r.wiki],
        "news": [asdict(n) for n in r.news],
    }


def _deserialize(data: dict) -> EnrichmentResult:
    return EnrichmentResult(
        market=[
            MarketChart(
                ticker=m["ticker"],
                current_price=m.get("current_price"),
                pct_change_30d=m.get("pct_change_30d"),
                chart_png=_from_b64(m.get("chart_png")),
                annotation=m.get("annotation", ""),
            )
            for m in data.get("market") or []
        ],
        macro=[
            MacroSeries(
                series_id=m["series_id"],
                name=m["name"],
                latest_value=m.get("latest_value"),
                latest_date=m.get("latest_date"),
                chart_png=_from_b64(m.get("chart_png")),
                annotation=m.get("annotation", ""),
            )
            for m in data.get("macro") or []
        ],
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
    market_entities: list[str],
    macro_indicators: list[str],
    named_entities: list[str],
    episode_pub_date: str | None,
    accent_hex: str,
) -> EnrichmentResult:
    if not enrichers:
        return EnrichmentResult()
    tasks = [
        e.enrich(
            market_entities=market_entities,
            macro_indicators=macro_indicators,
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
        merged.market.extend(p.market)
        merged.macro.extend(p.macro)
        merged.wiki.extend(p.wiki)
        merged.news.extend(p.news)
    return merged


def run_enrichers(
    enrichers: list[Enricher],
    *,
    notes_dir: Path,
    episode_id: str,
    market_entities: list[str],
    macro_indicators: list[str],
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
            market_entities=market_entities,
            macro_indicators=macro_indicators,
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
    can read the annotated cache rather than re-prompting Gemma 4."""
    cache = _cache_path(notes_dir, episode_id)
    try:
        cache.write_text(json.dumps(_serialize(result)), encoding="utf-8")
    except Exception as e:
        log.warning("Enrichment cache rewrite failed: %s", e)
