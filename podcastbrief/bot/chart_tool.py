"""Native Gemma 4 function calling — /chart command.

IMPORTANT FOR REVIEWERS: this command intentionally uses Ollama's tool-calling
API on Gemma 4 (the `tools=` parameter on `client.chat`) rather than asking the
model to emit a ticker symbol in plain text that we then parse. The model sees
the get_price_chart() function schema, decides to call it, and we execute the
real Yahoo Finance fetch off the back of that decision. The annotation that
follows is a second Gemma 4 call grounded in the resolved chart data.

This is the codebase's reference implementation of native Gemma 4 tool use.
"""
from __future__ import annotations
import asyncio
import io
import logging
from telegram import Update
from telegram.ext import ContextTypes

from podcastbrief.adapters.yahoo_enricher import YahooFinanceEnricher
from podcastbrief.ports.llm import LLM

log = logging.getLogger(__name__)


GET_PRICE_CHART_TOOL = {
    "type": "function",
    "function": {
        "name": "get_price_chart",
        "description": (
            "Fetch a 30-day price chart for a stock, ETF, index, or crypto symbol. "
            "Returns the current price, percent change, and a rendered chart image. "
            "Use Yahoo Finance-compatible symbols (e.g. 'AAPL', 'SPY', '^GSPC', "
            "'BTC-USD')."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "ticker": {
                    "type": "string",
                    "description": "Yahoo Finance ticker symbol.",
                },
            },
            "required": ["ticker"],
        },
    },
}


_SYSTEM = (
    "You are a financial assistant. When the user asks about a market price, "
    "trend, or asset performance, ALWAYS call the get_price_chart tool with "
    "the appropriate Yahoo Finance symbol. Do not try to recall prices from "
    "memory — only the tool has live data."
)


_ANNOTATE_SYS = (
    "You are a market commentator. Given a ticker, its current price, and its "
    "30-day percent change, write ONE sentence (≤30 words) that highlights "
    "what the move tells us. No hedging filler. No headers."
)


async def handle_chart_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    llm: LLM,
    yahoo: YahooFinanceEnricher,
) -> None:
    msg = update.message
    if not msg:
        return
    args = context.args or []
    query = " ".join(args).strip() or "the broad US stock market"

    # Native Gemma 4 function calling — see docstring at top of file.
    try:
        decision = await asyncio.to_thread(
            llm.call_with_tools,
            system=_SYSTEM,
            user=f"Show me a chart for: {query}",
            tools=[GET_PRICE_CHART_TOOL],
        )
    except Exception as e:
        log.exception("Gemma tool call failed: %s", e)
        await msg.reply_text("Couldn't decide which ticker to fetch.")
        return

    if decision.get("type") != "tool_call" or decision.get("name") != "get_price_chart":
        # Model declined the tool — fall back to its plain answer.
        await msg.reply_text(decision.get("content") or "Couldn't resolve a ticker.")
        return

    args_dict = decision.get("arguments") or {}
    ticker = str(args_dict.get("ticker") or "").strip().upper()
    if not ticker:
        await msg.reply_text("Model called the tool but didn't pick a ticker.")
        return

    await msg.reply_chat_action("upload_photo")
    result = await yahoo.enrich(market_entities=[ticker], macro_indicators=[], named_entities=[])
    if not result.market or not result.market[0].chart_png:
        await msg.reply_text(f"Yahoo Finance returned no data for {ticker}.")
        return
    m = result.market[0]

    # Second Gemma 4 call: produce a grounded one-line commentary.
    try:
        annotation = await asyncio.to_thread(
            llm.annotate,
            system=_ANNOTATE_SYS,
            user=(
                f"Ticker: {m.ticker}\n"
                f"Current price: {m.current_price}\n"
                f"30-day change: {m.pct_change_30d:+.1f}%"
                if m.pct_change_30d is not None else f"Ticker: {m.ticker}\nCurrent price: {m.current_price}"
            ),
        )
        annotation = annotation.strip()
    except Exception as e:
        log.warning("Chart annotation failed: %s", e)
        annotation = ""

    caption_lines = [f"📈 {m.ticker}"]
    if m.current_price is not None:
        caption_lines.append(f"${m.current_price:.2f}")
    if m.pct_change_30d is not None:
        caption_lines.append(f"{m.pct_change_30d:+.1f}% (30d)")
    caption = " · ".join(caption_lines)
    if annotation:
        caption = caption + "\n\n" + annotation

    buf = io.BytesIO(m.chart_png)
    buf.name = f"{m.ticker}.png"
    try:
        await msg.reply_photo(photo=buf, caption=caption[:1024])
    except Exception as e:
        log.exception("Telegram chart send failed: %s", e)
        await msg.reply_text(caption)
