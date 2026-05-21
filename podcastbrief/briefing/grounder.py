"""Pass 3 — grounded annotations.

After Pass 1 and Pass 2 have built the structured brief, and after the enrichers
have pulled real-world data (Wikipedia summaries, contemporaneous RSS headlines),
Gemma compares what the episode CLAIMED to what contemporaneous reporting SHOWS.

Each news article gets one annotation: does this headline support, complicate,
or contradict the brief's framing? That's the grounding step — turning a passive
transcript summary into a claim-checked brief that cites the world outside the
episode itself.

If the call fails or the model returns junk, annotations stay empty and the
PDF still renders cleanly — the cards just won't have a grounding line.
"""
from __future__ import annotations
import json
import logging
from pydantic import BaseModel, Field

from podcastbrief.briefing.schemas import BriefFinal
from podcastbrief.ports.enricher import EnrichmentResult
from podcastbrief.ports.llm import LLM

log = logging.getLogger(__name__)


class _Annotation(BaseModel):
    key: str    # article URL
    text: str = Field("", max_length=300)


class _Grounding(BaseModel):
    news: list[_Annotation] = Field(default_factory=list)


_SYSTEM = """You are a senior research editor cross-checking a transcript brief against contemporaneous reporting.

You receive:
1. The episode's structured brief (claims, predictions, key quotes).
2. Recent news headlines and snippets from the same time window.

For EACH news article, write ONE sentence that connects it to the brief: does the headline
support the speaker's framing, add nuance, or contradict it? Be specific — cite the article's
angle and the brief's claim. If the article is unrelated to the brief, say so briefly.

Length: 1 sentence per article, max 30 words. No hedging filler.

Return ONLY valid JSON matching the requested shape."""


_EXAMPLE = """{"news":[{"key":"https://example.com/article","text":"Reuters headline from the same week directly corroborates the guest's claim that chip shortages are easing in automotive supply chains."}]}"""


def _shape_input(brief: BriefFinal, e: EnrichmentResult) -> str:
    payload = {
        "brief": {
            "headline": brief.headline,
            "tldr": brief.tldr,
            "thesis": brief.thesis,
            "predictions": brief.predictions,
            "by_the_numbers": [d.model_dump() for d in brief.by_the_numbers],
            "pull_quotes": [q.model_dump() for q in brief.pull_quotes],
        },
        "news": [
            {
                "key": a.url,
                "title": a.title,
                "source": a.source,
                "date": a.pub_date,
                "snippet": a.summary,
            }
            for a in e.news
        ],
    }
    return json.dumps(payload, ensure_ascii=False)


def ground_enrichment(
    *, llm: LLM, brief: BriefFinal, enrichment: EnrichmentResult
) -> EnrichmentResult:
    """Run Pass 3. Mutates `enrichment` in place to add `.annotation` strings on
    news articles, returns it for chaining."""
    if not enrichment.news:
        return enrichment

    user_msg = _shape_input(brief, enrichment)
    from podcastbrief.briefing.extractor import language_directive
    try:
        result = llm.json_complete(
            system=_SYSTEM + language_directive(getattr(brief, "language", "en")),
            user=user_msg,
            schema=_Grounding,
            example=_EXAMPLE,
            temperature=0.3,
        )
    except Exception as e:
        log.warning("Pass 3 grounding call failed: %s", e)
        return enrichment

    by_url = {a.key: a.text for a in result.news}
    for n in enrichment.news:
        n.annotation = by_url.get(n.url, "")

    return enrichment
