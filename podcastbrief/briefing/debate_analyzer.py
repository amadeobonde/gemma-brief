"""Gemma 4 text synthesis for the /debate command.

After clips are selected, this module produces a clean three-section markdown
summary (agreement / divergence / open question) for Telegram. Wrapped in a
strict timeout with a graceful fallback message.
"""
from __future__ import annotations
import concurrent.futures
import logging

from podcastbrief.ports.debate_retriever import TopicMoment
from podcastbrief.ports.llm import LLM

log = logging.getLogger(__name__)


_ANALYSIS_SYS = """You analyze a debate compilation drawn from multiple podcast episodes.

Output EXACTLY three sections in this exact markdown format:

**Where they agree:** [1-2 sentences on genuine consensus across hosts]
**Where they diverge:** [2-3 sentences on the core disagreement — be specific about who said what]
**The open question:** [1 sentence — the thing none of them resolved]

Rules:
- Be direct. Do not hedge.
- Name hosts. Quote specific claims.
- No preamble, no headers above these three lines, no closing remarks.
- If only one host weighed in, write "**Where they agree:** Only one host weighed in here." for that section."""


_FALLBACK = (
    "**Where they agree:** _(analysis unavailable — the clips speak for themselves)_\n"
    "**Where they diverge:** _Listen to the voice note above for each host's take._\n"
    "**The open question:** _What do you think? Reply and I'll dig deeper._"
)


def synthesize_debate_analysis(
    *,
    llm: LLM,
    topic: str,
    moments: list[TopicMoment],
    timeout_seconds: float = 60.0,
) -> str:
    """Return a Telegram-ready markdown analysis. Never raises — falls back on error."""
    if not moments:
        return _FALLBACK

    payload = _build_payload(topic, moments)
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                llm.complete,
                system=_ANALYSIS_SYS,
                user=payload,
                temperature=0.3,
                num_predict=512,
            )
            result = future.result(timeout=timeout_seconds)
    except concurrent.futures.TimeoutError:
        log.warning("Debate analysis timed out after %.0fs — using fallback.", timeout_seconds)
        return _FALLBACK
    except Exception as e:
        log.warning("Debate analysis failed (%s) — using fallback.", e)
        return _FALLBACK

    text = (result or "").strip()
    if "**Where they agree" not in text:
        log.warning("Debate analysis missing required sections — using fallback.")
        return _FALLBACK
    return text


def _build_payload(topic: str, moments: list[TopicMoment]) -> str:
    lines = [f"Topic: {topic}", "", "Clips in this compilation:"]
    for i, m in enumerate(moments, 1):
        lines.append(
            f"{i}. [{m.host_name} — {m.episode_title}] stance={m.stance}\n"
            f"   \"{m.transcript_text.strip()}\""
        )
    return "\n".join(lines)
