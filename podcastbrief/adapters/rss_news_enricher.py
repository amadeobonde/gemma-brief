"""RSS news enrichment: top-3 contemporaneous headlines that share keywords
with the episode's entities/indicators.

Polls a configurable list of RSS feeds (default: Reuters Business, FT, BBC
Business, BBC World), filters to articles published within +/- 7 days of the
episode publish date, and scores each by keyword overlap. Returns the top 3.
"""
from __future__ import annotations
import asyncio
import logging
import re
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime

import feedparser
import httpx

from podcastbrief.ports.enricher import EnrichmentResult, NewsArticle

log = logging.getLogger(__name__)


_PER_FEED_TIMEOUT_S = 6
_MAX_ARTICLES = 3
_WINDOW_DAYS = 7
_STOP = {
    "the", "and", "for", "from", "with", "into", "your", "this", "that",
    "are", "was", "were", "but", "not", "you", "they", "their", "have",
    "has", "had", "will", "would", "could", "should", "about", "what",
    "when", "where", "why", "how", "all", "any", "some", "more", "most",
    "much", "many",
}


def _tokenize(s: str) -> set[str]:
    return {t for t in re.findall(r"[a-zA-Z0-9]+", s.lower()) if len(t) > 2 and t not in _STOP}


def _parse_pub_date(entry) -> datetime | None:
    for key in ("published", "updated", "pubDate"):
        v = entry.get(key)
        if not v:
            continue
        try:
            return parsedate_to_datetime(v).replace(tzinfo=None)
        except (TypeError, ValueError):
            pass
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00")).replace(tzinfo=None)
        except (TypeError, ValueError):
            pass
    return None


async def _fetch_feed(client: httpx.AsyncClient, url: str) -> list:
    try:
        r = await client.get(url, timeout=_PER_FEED_TIMEOUT_S, follow_redirects=True)
        r.raise_for_status()
        feed = feedparser.parse(r.content)
        return list(feed.entries) or []
    except Exception as e:
        log.warning("RSS fetch failed for %s: %s", url, e)
        return []


def _source_from_url(url: str) -> str:
    if "reuters" in url:
        return "Reuters"
    if "ft.com" in url:
        return "FT"
    if "bbc" in url:
        return "BBC"
    if "cnbc" in url:
        return "CNBC"
    if "bloomberg" in url:
        return "Bloomberg"
    return url.split("//", 1)[-1].split("/", 1)[0]


def _summary_snippet(entry, max_chars: int = 220) -> str:
    raw = entry.get("summary") or entry.get("description") or ""
    text = re.sub(r"<[^>]+>", "", raw)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_chars:
        text = text[: max_chars].rsplit(" ", 1)[0] + "…"
    return text


class RSSNewsEnricher:
    def __init__(self, *, feeds: list[str]) -> None:
        self.feeds = feeds

    async def enrich(
        self,
        *,
        named_entities: list[str] = (),
        episode_pub_date: str | None = None,
        accent_hex: str = "#6c63ff",
    ) -> EnrichmentResult:
        if not self.feeds:
            return EnrichmentResult()
        keywords = _tokenize(" ".join(list(named_entities)))
        if not keywords:
            return EnrichmentResult()

        anchor: datetime | None = None
        if episode_pub_date:
            try:
                anchor = parsedate_to_datetime(episode_pub_date).replace(tzinfo=None)
            except (TypeError, ValueError):
                try:
                    anchor = datetime.fromisoformat(
                        episode_pub_date.replace("Z", "+00:00")
                    ).replace(tzinfo=None)
                except (TypeError, ValueError):
                    anchor = None
        if anchor is None:
            anchor = datetime.utcnow()
        window = timedelta(days=_WINDOW_DAYS)

        async with httpx.AsyncClient() as client:
            feed_results = await asyncio.gather(
                *(_fetch_feed(client, u) for u in self.feeds), return_exceptions=False
            )

        scored: list[NewsArticle] = []
        for url, entries in zip(self.feeds, feed_results):
            source = _source_from_url(url)
            for e in entries:
                pub = _parse_pub_date(e)
                if pub and abs((pub - anchor).total_seconds()) > window.total_seconds():
                    continue
                title = str(e.get("title") or "")
                summary = _summary_snippet(e)
                if not title:
                    continue
                overlap = _tokenize(title + " " + summary) & keywords
                if not overlap:
                    continue
                scored.append(
                    NewsArticle(
                        title=title,
                        source=source,
                        pub_date=pub.strftime("%Y-%m-%d") if pub else "",
                        url=str(e.get("link") or ""),
                        summary=summary,
                        score=len(overlap) / max(len(keywords), 1),
                    )
                )
        scored.sort(key=lambda a: a.score, reverse=True)
        return EnrichmentResult(news=scored[:_MAX_ARTICLES])
