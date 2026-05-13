from __future__ import annotations
from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class MarketChart:
    ticker: str
    current_price: float | None
    pct_change_30d: float | None
    chart_png: bytes | None     # rendered chart, ready to embed in PDF
    annotation: str = ""         # Pass-3 grounding comparison vs episode claims


@dataclass
class MacroSeries:
    series_id: str               # FRED ID (e.g. "CPIAUCSL")
    name: str
    latest_value: float | None
    latest_date: str | None
    chart_png: bytes | None
    annotation: str = ""


@dataclass
class WikiEntity:
    name: str
    summary: str                 # 1-2 sentences
    url: str
    thumbnail_url: str | None = None


@dataclass
class NewsArticle:
    title: str
    source: str
    pub_date: str
    url: str
    summary: str
    score: float = 0.0           # relevance score (0-1)
    annotation: str = ""


@dataclass
class EnrichmentResult:
    market: list[MarketChart] = field(default_factory=list)
    macro: list[MacroSeries] = field(default_factory=list)
    wiki: list[WikiEntity] = field(default_factory=list)
    news: list[NewsArticle] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.market or self.macro or self.wiki or self.news)


class Enricher(Protocol):
    """Single-method protocol. Adapters take whatever subset of the brief they
    need (tickers, FRED IDs, entities, dates) and return their slice of the
    EnrichmentResult. The Pipeline runs them in parallel and merges results."""

    async def enrich(
        self,
        *,
        market_entities: list[str],
        macro_indicators: list[str],
        named_entities: list[str],
        episode_pub_date: str | None,
        accent_hex: str = "#6c63ff",
    ) -> EnrichmentResult: ...
