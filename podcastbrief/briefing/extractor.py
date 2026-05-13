from __future__ import annotations
import logging
from podcastbrief.core.models import Transcript
from podcastbrief.ports.llm import LLM
from podcastbrief.briefing.schemas import EpisodeStructure, Quote

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

ENRICHMENT IDENTIFIERS — return standardized symbols, NOT natural language:

market_entities: Yahoo Finance-compatible tickers for any stocks, ETFs, indices,
crypto, or forex mentioned. Examples:
  S&P 500 -> "^GSPC" or "SPY"
  Nasdaq 100 -> "^NDX" or "QQQ"
  10-year Treasury yield -> "^TNX"
  Apple -> "AAPL"
  Bitcoin -> "BTC-USD"
  Tesla -> "TSLA"
Use exactly the Yahoo symbol; do not invent.

macro_indicators: FRED series IDs for any macroeconomic concept discussed.
Examples:
  CPI / inflation -> "CPIAUCSL"
  Unemployment rate -> "UNRATE"
  10-year Treasury rate -> "DGS10"
  Fed funds rate -> "FEDFUNDS"
  Real GDP -> "GDPC1"
  Nominal GDP -> "GDP"
  Industrial production -> "INDPRO"
  Retail sales -> "RSAFS"
  M2 money supply -> "M2SL"
Skip if the episode is not macro-relevant. Do not invent IDs.

named_entities: People, events, companies, places, or concepts worth a Wikipedia
lookup. Plain names: "Federal Reserve", "Cathie Wood", "2008 financial crisis",
"index fund", "S&P 500". Max 8.

socratic_hooks: Exactly 3 questions the host raises but never fully resolves in
the episode. These are the open threads — not rhetorical, genuinely unresolved.
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


_LANG_NAMES = {
    "en": "English", "es": "Spanish", "fr": "French", "de": "German",
    "pt": "Portuguese", "it": "Italian", "nl": "Dutch", "ja": "Japanese",
    "zh": "Chinese", "ko": "Korean", "ru": "Russian", "ar": "Arabic",
    "hi": "Hindi", "tr": "Turkish", "pl": "Polish", "sv": "Swedish",
    "id": "Indonesian", "vi": "Vietnamese", "uk": "Ukrainian", "el": "Greek",
}


def language_directive(lang_code: str) -> str:
    """A one-line system-prompt addendum binding the model to the transcript's
    language. Threaded through every Gemma 4 call in the pipeline so non-English
    episodes get non-English briefs."""
    code = (lang_code or "en").split("-")[0].lower()
    name = _LANG_NAMES.get(code, code.upper())
    if code == "en":
        return ""
    return (
        f"\n\nLANGUAGE: The transcript is in {name}. Respond ENTIRELY in {name}: "
        f"all field values, section content, quote context, headlines, and "
        f"annotations. Do not switch back to English. Speaker labels like "
        f"'Host' / 'Guest' should also be the {name} equivalents if natural."
    )


# Concrete shape example for Pass 1. Without this, json_complete falls back to
# dumping the raw JSON schema in the system prompt, and on richer schemas like
# EpisodeStructure Gemma 4 occasionally echoes the schema definition itself
# back instead of producing an instance (returns {"$defs": ..., "title":
# "EpisodeStructure"}). Showing one filled-in shape stops that drift cold —
# same trick that fixed Pass 2.
EPISODE_STRUCTURE_EXAMPLE = """{"tldr":"One-sentence summary of the episode.","thesis":"The central argument the host is making, in <=40 words.","why_it_matters":["First bullet, <=25 words.","Second bullet.","Third bullet."],"candidate_quotes":[{"text":"verbatim quote from the transcript","speaker":"Host Name","role":"host","timestamp":"24:18","context":"why this quote matters","impact_score":9},{"text":"another verbatim quote","speaker":"Guest Name","role":"guest","timestamp":"41:02","context":"why this matters","impact_score":8},{"text":"third quote","speaker":"Host","role":"host","timestamp":"58:47","context":"why","impact_score":7}],"by_the_numbers":[{"stat":"$42B","label":"market size","source":"speaker","why_relevant":"why this number matters"}],"hosts":["Wilfred Frost"],"guests":["Dan Niles"],"resources_mentioned":[{"name":"S&P 500","kind":"other","note":"benchmark"}],"predictions":["Forward-looking claim from the host."],"counterpoints":["Tension or counter-argument raised."],"action_items":["What a listener could do."],"topics":["ai","markets","macro"],"go_deeper":["search term 1","search term 2"],"market_entities":["SPY","^GSPC","NVDA"],"macro_indicators":["CPIAUCSL","DGS10"],"named_entities":["Federal Reserve","Dan Niles"],"socratic_hooks":["A question the host raises but does not answer.","Another open question.","A third unresolved question."]}"""


def _minimal_fallback_structure(
    transcript: Transcript, *, show_name: str, episode_title: str
) -> EpisodeStructure:
    """Last-resort EpisodeStructure built mechanically from the transcript when
    Gemma 4 refuses to produce a parseable Pass-1 payload after retries. Lets
    the pipeline still ship a PDF rather than dropping the episode entirely."""
    head = (transcript.text or "").strip()
    snippet = head[:280] + ("…" if len(head) > 280 else "")
    # Pull a few segment texts as placeholder candidate quotes.
    quotes: list[Quote] = []
    for seg in (transcript.segments or [])[:3]:
        text = (seg.text or "").strip()
        if not text:
            continue
        mm = int(seg.start) // 60
        ss = int(seg.start) % 60
        quotes.append(
            Quote(
                text=text,
                speaker="Speaker",
                role="other",
                timestamp=f"{mm:02d}:{ss:02d}",
                context="",
                impact_score=5,
            )
        )
    while len(quotes) < 3:
        quotes.append(
            Quote(
                text=snippet or "See transcript.",
                speaker="Speaker",
                role="other",
                context="",
                impact_score=4,
            )
        )
    return EpisodeStructure(
        tldr=snippet or f"Brief generation failed for {episode_title}; raw transcript available.",
        thesis=f"Auto-generated fallback brief for {show_name} — {episode_title}.",
        why_it_matters=[
            "Pass-1 extraction did not return a valid structured payload.",
            "Transcript is preserved in the markdown note for manual review.",
            "Try /run to retry with a fresh Whisper pass and a warmer model.",
        ],
        candidate_quotes=quotes,
    )


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
    lang = (transcript.language or "en").split("-")[0].lower()
    system = SYSTEM_EXTRACT + language_directive(lang)
    user = (
        f"SHOW: {show_name}\n"
        f"EPISODE: {episode_title}\n\n"
        f"TRANSCRIPT (with timestamps):\n{transcript_text}"
    )
    try:
        structure = llm.json_complete(
            system=system,
            user=user,
            schema=EpisodeStructure,
            example=EPISODE_STRUCTURE_EXAMPLE,
            max_retries=2,
        )
    except Exception as e:
        log.warning(
            "Pass 1 (EpisodeStructure) failed after retries — falling back to "
            "minimal structure so the pipeline still ships a PDF: %s",
            e,
        )
        structure = _minimal_fallback_structure(
            transcript, show_name=show_name, episode_title=episode_title
        )

    if artwork_png:
        try:
            caption = llm.complete(
                system="You are a concise visual describer." + language_directive(lang),
                user=VISION_PROMPT,
                images=[artwork_png],
                temperature=0.3,
            ).strip()
            structure.visual_caption = caption[:200]
        except Exception as e:  # vision pass is non-critical
            log.warning("Vision caption failed: %s", e)

    return structure
