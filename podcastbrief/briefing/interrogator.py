from __future__ import annotations
import json
import logging
from typing import Callable, TypeVar
from pydantic import BaseModel, Field
from podcastbrief.core.models import Transcript
from podcastbrief.ports.llm import LLM
from podcastbrief.briefing.schemas import (
    BriefFinal,
    DataPoint,
    EpisodeStructure,
    Quote,
    Resource,
)

T = TypeVar("T", bound=BaseModel)

log = logging.getLogger(__name__)


SYSTEM_INTERROGATE = """You are a senior editor sharpening a structured podcast brief.

For each focused question you receive, return ONLY the requested JSON output. No prose, no explanation, NO JSON-schema metadata. Output the data values, not the schema.

Be ruthless about quality:
- Quotes must be verbatim against the transcript and correctly attributed.
- Bullets must make a concrete claim a reader could repeat.
- A headline is not the episode title — it captures the most newsworthy claim.
"""


# Sub-schemas (no underscore prefix — keeps Gemma from copying it as a wrapper key)

class QuoteSelection(BaseModel):
    pull_quotes: list[Quote] = Field(default_factory=list, max_length=5)


class BulletReplacement(BaseModel):
    why_it_matters: list[str] = Field(default_factory=list, max_length=6)


class DataPoints(BaseModel):
    by_the_numbers: list[DataPoint] = Field(default_factory=list, max_length=5)


class ResourceList(BaseModel):
    resources_mentioned: list[Resource] = Field(default_factory=list)


class Headline(BaseModel):
    headline: str = Field("", max_length=120)


# Concrete shape examples — far more reliable than dumping the JSON schema.

EXAMPLE_QUOTE_SELECTION = """{"pull_quotes":[{"text":"verbatim quote","speaker":"Speaker Name","role":"host","timestamp":"24:18","context":"why this matters","impact_score":9}]}"""

EXAMPLE_BULLET_REPLACEMENT = """{"why_it_matters":["bullet one","bullet two","bullet three"]}"""

EXAMPLE_DATA_POINTS = """{"by_the_numbers":[{"stat":"$42B","label":"market size","source":"speaker name","why_relevant":"why this number matters"}]}"""

EXAMPLE_RESOURCES = """{"resources_mentioned":[{"name":"Resource Name","kind":"book","note":"short note"}]}"""

EXAMPLE_HEADLINE = """{"headline":"Punchy newsworthy claim in twelve words or fewer"}"""


def _try(fn: Callable[[], T], fallback: T, label: str) -> T:
    try:
        return fn()
    except Exception as e:
        log.warning("Pass-2 %s failed, using pass-1 fallback: %s", label, e)
        return fallback


def interrogate(
    *,
    llm: LLM,
    structure: EpisodeStructure,
    transcript: Transcript,
) -> BriefFinal:
    from podcastbrief.briefing.extractor import _bound_transcript, language_directive

    transcript_text = _bound_transcript(transcript.with_timestamps())
    lang = (transcript.language or "en").split("-")[0].lower()
    system_prompt = SYSTEM_INTERROGATE + language_directive(lang)

    # ---- 1. Quote selection ----
    pass1_quotes = sorted(
        structure.candidate_quotes, key=lambda q: q.impact_score, reverse=True
    )[:5]
    fallback_selection = QuoteSelection(pull_quotes=pass1_quotes[:5])
    selection = _try(
        lambda: llm.json_complete(
            system=system_prompt,
            user=(
                "From the candidate_quotes below, select the 3-5 STRONGEST by impact and "
                "distinctness. Discard duplicates and weak ones. Each quote must be verbatim "
                "from the transcript with correct speaker. Preserve timestamps.\n\n"
                f"CANDIDATE_QUOTES:\n{json.dumps([q.model_dump() for q in structure.candidate_quotes])}\n\n"
                f"TRANSCRIPT:\n{transcript_text}"
            ),
            schema=QuoteSelection,
            example=EXAMPLE_QUOTE_SELECTION,
        ),
        fallback_selection,
        "quote selection",
    )

    # ---- 2. Bullet sharpening ----
    fallback_bullets = BulletReplacement(why_it_matters=structure.why_it_matters[:6])
    bullets = _try(
        lambda: llm.json_complete(
            system=system_prompt,
            user=(
                "Identify the WEAKEST bullet in the list below and replace it with a sharper, "
                "more concrete claim grounded directly in the transcript. Keep all other bullets. "
                "Return the FULL updated list (3-5 bullets).\n\n"
                f"CURRENT_BULLETS: {json.dumps(structure.why_it_matters)}\n\n"
                f"TRANSCRIPT:\n{transcript_text}"
            ),
            schema=BulletReplacement,
            example=EXAMPLE_BULLET_REPLACEMENT,
        ),
        fallback_bullets,
        "bullet sharpening",
    )

    # ---- 3. Data enrichment ----
    fallback_data = DataPoints(by_the_numbers=structure.by_the_numbers[:5])
    data = _try(
        lambda: llm.json_complete(
            system=system_prompt,
            user=(
                "Re-scan the transcript for concrete numbers, percentages, dates, or dollar "
                "figures missing from the list below. Add up to 2 more if supported. Return the "
                "FULL updated list (existing + new), capped at 5.\n\n"
                f"CURRENT_NUMBERS: {json.dumps([d.model_dump() for d in structure.by_the_numbers])}\n\n"
                f"TRANSCRIPT:\n{transcript_text}"
            ),
            schema=DataPoints,
            example=EXAMPLE_DATA_POINTS,
        ),
        fallback_data,
        "data enrichment",
    )

    # ---- 4. Resources sweep ----
    fallback_res = ResourceList(resources_mentioned=structure.resources_mentioned)
    resources = _try(
        lambda: llm.json_complete(
            system=system_prompt,
            user=(
                "Scan the transcript for books, papers, tools, companies, or notable people "
                "missing from the list below. Add them with kind and short note. Return the "
                "FULL updated list (existing + new).\n\n"
                f"CURRENT_RESOURCES: {json.dumps([r.model_dump() for r in structure.resources_mentioned])}\n\n"
                f"TRANSCRIPT:\n{transcript_text}"
            ),
            schema=ResourceList,
            example=EXAMPLE_RESOURCES,
        ),
        fallback_res,
        "resources sweep",
    )

    # ---- 5. Headline ----
    fallback_headline = Headline(headline=(structure.tldr or "Daily Brief")[:80])
    headline = _try(
        lambda: llm.json_complete(
            system=system_prompt,
            user=(
                "Write a punchy ≤12-word headline for this brief — NOT the episode title — that "
                "captures the most newsworthy claim. Active voice, concrete.\n\n"
                f"TLDR: {structure.tldr}\n"
                f"THESIS: {structure.thesis}\n"
                f"BULLETS: {json.dumps(structure.why_it_matters)}"
            ),
            schema=Headline,
            example=EXAMPLE_HEADLINE,
        ),
        fallback_headline,
        "headline",
    )

    # Pull-quote fallback if selection somehow returned empty.
    final_quotes = selection.pull_quotes or pass1_quotes[:3]
    if not final_quotes:
        # Synthesize a minimal placeholder so the brief still renders.
        final_quotes = [
            Quote(
                text=structure.tldr or "Listen to the episode for the full context.",
                speaker="Episode",
                role="other",
                context="",
                impact_score=5,
            )
        ]

    return BriefFinal(
        headline=headline.headline or fallback_headline.headline,
        tldr=structure.tldr,
        thesis=structure.thesis,
        why_it_matters=bullets.why_it_matters or structure.why_it_matters,
        pull_quotes=final_quotes,
        by_the_numbers=(data.by_the_numbers or structure.by_the_numbers)[:5],
        hosts=structure.hosts,
        guests=structure.guests,
        resources_mentioned=resources.resources_mentioned or structure.resources_mentioned,
        predictions=structure.predictions,
        counterpoints=structure.counterpoints,
        action_items=structure.action_items,
        topics=structure.topics,
        go_deeper=structure.go_deeper,
        visual_caption=structure.visual_caption,
        # Pass enrichment hooks through unchanged — Pass 2 doesn't refine these.
        market_entities=structure.market_entities,
        macro_indicators=structure.macro_indicators,
        named_entities=structure.named_entities,
        socratic_hooks=structure.socratic_hooks,
    )
