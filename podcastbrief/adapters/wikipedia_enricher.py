"""Wikipedia REST API enrichment: short summary per named entity.

Endpoint: https://en.wikipedia.org/api/rest_v1/page/summary/{title}
No API key required. Handles disambiguation pages by retrying with an extra
context word lifted from the entity itself.
"""
from __future__ import annotations
import asyncio
import logging
import urllib.parse

import httpx

from podcastbrief.ports.enricher import EnrichmentResult, WikiEntity

log = logging.getLogger(__name__)


_PER_ENTITY_TIMEOUT_S = 5
_MAX_ENTITIES = 8
_USER_AGENT = "podcastbrief/1.0 (https://github.com/amadeobonde/podcastbrief)"


async def _summary(client: httpx.AsyncClient, title: str) -> dict | None:
    safe = urllib.parse.quote(title.replace(" ", "_"), safe="")
    url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{safe}"
    try:
        r = await client.get(
            url,
            headers={"User-Agent": _USER_AGENT, "accept": "application/json"},
            follow_redirects=True,
            timeout=_PER_ENTITY_TIMEOUT_S,
        )
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.warning("Wikipedia fetch failed for %s: %s", title, e)
        return None


def _two_sentences(text: str) -> str:
    if not text:
        return ""
    # Cheap sentence splitter — good enough for summary text from Wikipedia.
    parts = []
    buf = []
    for ch in text:
        buf.append(ch)
        if ch in ".!?" and len(buf) > 10:
            parts.append("".join(buf).strip())
            buf = []
            if len(parts) >= 2:
                break
    if buf and len(parts) < 2:
        parts.append("".join(buf).strip())
    return " ".join(parts).strip()


async def _enrich_one(client: httpx.AsyncClient, entity: str) -> WikiEntity | None:
    data = await _summary(client, entity)
    if not data:
        return None
    if data.get("type") == "disambiguation":
        # Retry with the entity name appended to itself — Wikipedia will treat
        # the second token as context (e.g. "Mercury" -> "Mercury planet").
        # Crude but cheap. If still disambiguous, skip.
        retry = await _summary(client, f"{entity} (concept)")
        if not retry or retry.get("type") == "disambiguation":
            log.info("Wikipedia: skipping disambiguation page for %r", entity)
            return None
        data = retry
    summary = _two_sentences(str(data.get("extract") or ""))
    if not summary:
        return None
    return WikiEntity(
        name=str(data.get("title") or entity),
        summary=summary,
        url=str((data.get("content_urls") or {}).get("desktop", {}).get("page") or ""),
        thumbnail_url=str((data.get("thumbnail") or {}).get("source") or "") or None,
    )


class WikipediaEnricher:
    async def enrich(
        self,
        *,
        named_entities: list[str],
        episode_pub_date: str | None = None,
        accent_hex: str = "#6c63ff",
    ) -> EnrichmentResult:
        if not named_entities:
            return EnrichmentResult()
        entities = list(dict.fromkeys(named_entities))[:_MAX_ENTITIES]
        async with httpx.AsyncClient() as client:
            tasks = [_enrich_one(client, e) for e in entities]
            results = await asyncio.gather(*tasks, return_exceptions=False)
        out = [r for r in results if r is not None]
        return EnrichmentResult(wiki=out)
