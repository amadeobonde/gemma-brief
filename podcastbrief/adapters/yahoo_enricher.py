"""Yahoo Finance enrichment: 30-day price chart per ticker, rendered as a
minimal PNG ready to embed in the PDF.

We use yfinance for data and a non-interactive matplotlib backend so this runs
fine under launchd (no display). Each ticker gets a strict timeout — slow
upstream shouldn't block the pipeline.
"""
from __future__ import annotations
import asyncio
import io
import logging
from concurrent.futures import ThreadPoolExecutor

import matplotlib
matplotlib.use("Agg")  # headless — must come before pyplot
import matplotlib.pyplot as plt
import yfinance as yf

from podcastbrief.ports.enricher import EnrichmentResult, MarketChart

log = logging.getLogger(__name__)


_PER_TICKER_TIMEOUT_S = 10
_MAX_TICKERS = 4


def _fetch_one(ticker: str, accent_hex: str) -> MarketChart:
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="30d", auto_adjust=True)
        if hist is None or hist.empty:
            log.warning("Yahoo: no history for %s", ticker)
            return MarketChart(ticker=ticker, current_price=None, pct_change_30d=None, chart_png=None)
        closes = hist["Close"].dropna()
        if closes.empty:
            return MarketChart(ticker=ticker, current_price=None, pct_change_30d=None, chart_png=None)

        current = float(closes.iloc[-1])
        first = float(closes.iloc[0])
        pct = ((current - first) / first * 100.0) if first else None

        # Minimal-style chart: no gridlines, no borders, accent color line.
        fig, ax = plt.subplots(figsize=(6, 2.2), dpi=150)
        ax.plot(closes.index, closes.values, color=accent_hex, linewidth=2.4)
        ax.fill_between(
            closes.index, closes.values, closes.min(), color=accent_hex, alpha=0.08
        )
        for spine in ax.spines.values():
            spine.set_visible(False)
        ax.tick_params(left=False, bottom=False, labelleft=False, labelbottom=False)
        ax.margins(x=0, y=0.05)
        fig.tight_layout(pad=0.2)

        buf = io.BytesIO()
        fig.savefig(buf, format="png", transparent=True, bbox_inches="tight", pad_inches=0.05)
        plt.close(fig)
        return MarketChart(
            ticker=ticker,
            current_price=current,
            pct_change_30d=pct,
            chart_png=buf.getvalue(),
        )
    except Exception as e:
        log.warning("Yahoo fetch failed for %s: %s", ticker, e)
        return MarketChart(ticker=ticker, current_price=None, pct_change_30d=None, chart_png=None)


class YahooFinanceEnricher:
    async def enrich(
        self,
        *,
        market_entities: list[str],
        macro_indicators: list[str] = (),
        named_entities: list[str] = (),
        episode_pub_date: str | None = None,
        accent_hex: str = "#6c63ff",
    ) -> EnrichmentResult:
        if not market_entities:
            return EnrichmentResult()
        tickers = list(dict.fromkeys(market_entities))[:_MAX_TICKERS]
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor(max_workers=len(tickers)) as pool:
            tasks = [
                asyncio.wait_for(
                    loop.run_in_executor(pool, _fetch_one, t, accent_hex),
                    timeout=_PER_TICKER_TIMEOUT_S,
                )
                for t in tickers
            ]
            results: list[MarketChart] = []
            for coro, ticker in zip(asyncio.as_completed(tasks), tickers):
                try:
                    results.append(await coro)
                except asyncio.TimeoutError:
                    log.warning("Yahoo: timeout fetching %s", ticker)
                    results.append(
                        MarketChart(ticker=ticker, current_price=None, pct_change_30d=None, chart_png=None)
                    )
        # Drop entries with no chart so the PDF doesn't render empty cards.
        results = [r for r in results if r.chart_png is not None]
        return EnrichmentResult(market=results)
