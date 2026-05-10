from __future__ import annotations
import logging
from podcastbrief.core.models import Transcript
from podcastbrief.ports.llm import LLM
from podcastbrief.briefing.schemas import EpisodeStructure

log = logging.getLogger(__name__)


SYSTEM_EXTRACT = """You are a senior editor producing morning-brief source material from podcast transcripts.

Your job in this pass: extract a RICH, OVER-INCLUSIVE structured representation. Pass 2 will select and sharpen.

Rules:
- Quote text MUST be VERBATIM from the transcript. Do not paraphrase quotes.
- Attribute speaker accurately. If you cannot tell, use "Host" or "Guest" with role accordingly.
- Use the [MM:SS] timestamps in the transcript to populate `timestamp` for each quote.
- Extract 8-12 candidate_quotes. Score each on impact (1-10): 10 = most newsworthy, distinctive, quotable.
- For by_the_numbers, only include figures actually stated in the transcript (numbers, percentages, dates, dollar amounts).
- For resources_mentioned, capture every book/paper/tool/person/company/article cited.
- Be concrete. No filler, no editorializing.
"""

VISION_PROMPT = """Look at this podcast episode artwork. In ≤30 words, describe what it visually communicates about the show or this episode (mood, subject, characters, design choices). One sentence."""


# Hard ceiling on transcript characters sent to the model. Roughly 4 chars/token,
# so 140k chars ≈ 35k tokens — fits comfortably in the 49k num_ctx along with
# schema, system prompt, and output budget. For longer episodes we keep the head
# (intro + first half) and the tail (conclusions, predictions) and drop the middle.
MAX_TRANSCRIPT_CHARS = 80_000


def _bound_transcript(text: str, *, max_chars: int = MAX_TRANSCRIPT_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    head = text[:half]
    tail = text[-half:]
    return f"{head}\n\n[... middle of transcript truncated for length ...]\n\n{tail}"


def extract_structure(
    *,
    llm: LLM,
    transcript: Transcript,
    show_name: str,
    episode_title: str,
    artwork_png: bytes | None = None,
) -> EpisodeStructure:
    transcript_text = _bound_transcript(transcript.with_timestamps())
    log.info("Transcript chars sent to model: %d", len(transcript_text))
    user = (
        f"SHOW: {show_name}\n"
        f"EPISODE: {episode_title}\n\n"
        f"TRANSCRIPT (with timestamps):\n{transcript_text}"
    )
    structure = llm.json_complete(
        system=SYSTEM_EXTRACT,
        user=user,
        schema=EpisodeStructure,
        max_retries=1,
    )

    if artwork_png:
        try:
            caption = llm.complete(
                system="You are a concise visual describer.",
                user=VISION_PROMPT,
                images=[artwork_png],
                temperature=0.3,
            ).strip()
            structure.visual_caption = caption[:200]
        except Exception as e:  # vision pass is non-critical
            log.warning("Vision caption failed: %s", e)

    return structure
