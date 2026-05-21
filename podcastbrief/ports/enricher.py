from __future__ import annotations
from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class WikiEntity:
    name: str
    summary: str         # 1-2 sentences
    url: str
    thumbnail_url: str | None = None


@dataclass
class NewsArticle:
    title: str
    source: str
    pub_date: str
    url: str
    summary: str
    score: float = 0.0    # relevance score (0-1)
    annotation: str = ""  # Pass-3 grounding: does this headline support or push back on the brief?


@dataclass
class EnrichmentResult:
    wiki: list[WikiEntity] = field(default_factory=list)
    news: list[NewsArticle] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.wiki or self.news)


class Enricher(Protocol):
    """Single-method protocol. Adapters take whatever subset of the brief they
    need (entities, dates) and return their slice of the EnrichmentResult.
    The Pipeline runs them in parallel and merges results."""

    async def enrich(
        self,
        *,
        named_entities: list[str],
        episode_pub_date: str | None,
        accent_hex: str = "#6c63ff",
    ) -> EnrichmentResult: ...
