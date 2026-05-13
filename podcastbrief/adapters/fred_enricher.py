"""FRED enrichment: 12-month series + sparkline for each macro indicator.

Uses the public FRED API (https://fred.stlouisfed.org/docs/api/fred/). Requires
a free API key; if FRED_API_KEY is empty the enricher returns an empty result
silently rather than blocking the pipeline.
"""
from __future__ import annotations
import asyncio
import io
import logging
from datetime import datetime, timedelta

import httpx
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from podcastbrief.ports.enricher import EnrichmentResult, MacroSeries

log = logging.getLogger(__name__)


_BASE = "https://api.stlouisfed.org/fred"
_PER_SERIES_TIMEOUT_S = 10
_MAX_SERIES = 3


async def _fetch_series_meta(client: httpx.AsyncClient, series_id: str, api_key: str) -> dict:
    r = await client.get(
        f"{_BASE}/series",
        params={"series_id": series_id, "api_key": api_key, "file_type": "json"},
        timeout=_PER_SERIES_TIMEOUT_S,
    )
    r.raise_for_status()
    data = r.json()
    return (data.get("seriess") or [{}])[0]


async def _fetch_observations(client: httpx.AsyncClient, series_id: str, api_key: str) -> list[dict]:
    start = (datetime.utcnow() - timedelta(days=400)).strftime("%Y-%m-%d")
    r = await client.get(
        f"{_BASE}/series/observations",
        params={
            "series_id": series_id,
            "api_key": api_key,
            "file_type": "json",
            "observation_start": start,
        },
        timeout=_PER_SERIES_TIMEOUT_S,
    )
    r.raise_for_status()
    return r.json().get("observations") or []


def _render_sparkline(values: list[float], accent_hex: str) -> bytes:
    fig, ax = plt.subplots(figsize=(4, 1.0), dpi=150)
    ax.plot(values, color=accent_hex, linewidth=2.0)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.tick_params(left=False, bottom=False, labelleft=False, labelbottom=False)
    ax.margins(x=0, y=0.08)
    fig.tight_layout(pad=0.0)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", transparent=True, bbox_inches="tight", pad_inches=0.02)
    plt.close(fig)
    return buf.getvalue()


async def _enrich_one(client: httpx.AsyncClient, series_id: str, api_key: str, accent_hex: str) -> MacroSeries | None:
    try:
        meta_task = asyncio.create_task(_fetch_series_meta(client, series_id, api_key))
        obs_task = asyncio.create_task(_fetch_observations(client, series_id, api_key))
        meta = await meta_task
        obs = await obs_task
        numeric = []
        last_value = None
        last_date = None
        for o in obs:
            v = o.get("value")
            try:
                fv = float(v)
            except (ValueError, TypeError):
                continue
            numeric.append(fv)
            last_value = fv
            last_date = o.get("date")
        if not numeric:
            return None
        png = _render_sparkline(numeric, accent_hex)
        return MacroSeries(
            series_id=series_id,
            name=str(meta.get("title") or series_id),
            latest_value=last_value,
            latest_date=last_date,
            chart_png=png,
        )
    except httpx.HTTPStatusError as e:
        log.warning("FRED HTTP %s for %s", e.response.status_code, series_id)
        return None
    except Exception as e:
        log.warning("FRED fetch failed for %s: %s", series_id, e)
        return None


class FREDEnricher:
    def __init__(self, *, api_key: str) -> None:
        self.api_key = (api_key or "").strip()

    async def enrich(
        self,
        *,
        market_entities: list[str] = (),
        macro_indicators: list[str],
        named_entities: list[str] = (),
        episode_pub_date: str | None = None,
        accent_hex: str = "#6c63ff",
    ) -> EnrichmentResult:
        if not self.api_key or not macro_indicators:
            return EnrichmentResult()
        series = list(dict.fromkeys(macro_indicators))[:_MAX_SERIES]
        async with httpx.AsyncClient() as client:
            tasks = [_enrich_one(client, sid, self.api_key, accent_hex) for sid in series]
            results = await asyncio.gather(*tasks, return_exceptions=False)
        out = [r for r in results if r is not None]
        return EnrichmentResult(macro=out)
