from __future__ import annotations
from pydantic import BaseModel, Field, field_validator, model_validator


_VALID_ROLES = {"host", "guest", "other"}
_VALID_KINDS = {"book", "paper", "tool", "person", "company", "article", "other"}

# Map model-emitted free-form labels onto the controlled vocabulary used by the
# renderer (which picks an icon per kind). Anything not listed snaps to "other"
# rather than failing pydantic validation.
_KIND_SYNONYMS = {
    # financial instruments / asset types
    "index": "other", "fund": "other", "etf": "other", "stock": "other",
    "bond": "other", "asset": "other", "security": "other", "currency": "other",
    "concept": "other", "framework": "other", "method": "other", "metric": "other",
    "principle": "other", "rule": "other",
    # tools / software / platforms
    "platform": "tool", "app": "tool", "software": "tool", "service": "tool",
    "website": "tool", "site": "tool", "product": "tool", "system": "tool",
    # publications
    "publication": "article", "podcast": "article", "blog": "article",
    "newsletter": "article", "video": "article", "show": "article",
    # research
    "study": "paper", "report": "paper", "research": "paper", "whitepaper": "paper",
    # people roles
    "researcher": "person", "author": "person", "expert": "person",
    "speaker": "person", "guest": "person", "host": "person", "founder": "person",
    "ceo": "person", "executive": "person", "investor": "person", "advisor": "person",
    # orgs
    "firm": "company", "organization": "company", "org": "company",
    "brand": "company", "agency": "company", "institution": "company",
    "bank": "company", "fund_company": "company",
}


def _word_count(s: str) -> int:
    return len(s.split())


def _normalize_kind(v: str) -> str:
    s = (v or "").strip().lower()
    if s in _VALID_KINDS:
        return s
    return _KIND_SYNONYMS.get(s, "other")


def _normalize_role(v: str) -> str:
    s = (v or "").strip().lower()
    if s in _VALID_ROLES:
        return s
    if "host" in s:
        return "host"
    if "guest" in s:
        return "guest"
    return "other"


def _unwrap_class(cls_name: str, data):
    """Strip {"ClassName": {...}} wrappers Gemma sometimes emits in JSON mode."""
    if isinstance(data, dict) and len(data) == 1:
        only_key = next(iter(data))
        if only_key.lower() == cls_name.lower() and isinstance(data[only_key], dict):
            return data[only_key]
    return data


class Quote(BaseModel):
    text: str = Field("", description="Verbatim quote text, trimmed to ≤40 words.")
    speaker: str = Field("Speaker", description="Name if known, else 'Host' or 'Guest'.")
    role: str = Field("other", description="One of: host, guest, other.")
    timestamp: str | None = Field(None, description="MM:SS, from Whisper segments if attributable.")
    context: str = Field("", description="≤20 words on why the quote matters.")
    impact_score: int = Field(5, ge=1, le=10)

    @model_validator(mode="before")
    @classmethod
    def _unwrap(cls, data):
        return _unwrap_class("Quote", data)

    @field_validator("role", mode="before")
    @classmethod
    def _coerce_role(cls, v) -> str:
        return _normalize_role(str(v) if v is not None else "other")

    @field_validator("impact_score", mode="before")
    @classmethod
    def _coerce_impact(cls, v):
        try:
            n = int(v)
        except (TypeError, ValueError):
            return 5
        return max(1, min(10, n))

    @field_validator("text")
    @classmethod
    def _trim_text(cls, v: str) -> str:
        words = v.split()
        if len(words) > 40:
            return " ".join(words[:40]) + "…"
        return v


class DataPoint(BaseModel):
    stat: str = Field("", description="The figure itself, e.g. '$42B', '17%', '2030'.")
    label: str = Field("", description="What the figure measures.")
    source: str | None = Field(None, description="Who/where it came from.")
    why_relevant: str = Field("", description="≤20 words.")

    @model_validator(mode="before")
    @classmethod
    def _unwrap(cls, data):
        return _unwrap_class("DataPoint", data)


class Resource(BaseModel):
    name: str = ""
    kind: str = Field(
        "other",
        description="One of: book, paper, tool, person, company, article, other.",
    )
    note: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _unwrap(cls, data):
        return _unwrap_class("Resource", data)

    @field_validator("kind", mode="before")
    @classmethod
    def _coerce_kind(cls, v) -> str:
        return _normalize_kind(str(v) if v is not None else "other")


class EpisodeStructure(BaseModel):
    """Pass-1 output. Pass 2 enriches/replaces fields."""

    tldr: str = Field(..., description="≤30 words.")
    thesis: str = Field(..., description="Central argument of the episode, ≤40 words.")
    why_it_matters: list[str] = Field(..., min_length=2, max_length=6)
    candidate_quotes: list[Quote] = Field(..., min_length=3, max_length=15)
    by_the_numbers: list[DataPoint] = Field(default_factory=list)
    hosts: list[str] = Field(default_factory=list)
    guests: list[str] = Field(default_factory=list)
    resources_mentioned: list[Resource] = Field(default_factory=list)
    predictions: list[str] = Field(default_factory=list)
    counterpoints: list[str] = Field(default_factory=list)
    action_items: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    go_deeper: list[str] = Field(default_factory=list)
    visual_caption: str | None = None

    # ---- enrichment hooks (drive Yahoo / FRED / Wikipedia / RSS lookups) ----
    # Standardized identifiers, NOT natural language. The extractor prompt
    # explicitly asks the model for these formats so downstream adapters can
    # call APIs without a normalization step.
    market_entities: list[str] = Field(
        default_factory=list,
        description="Yahoo Finance-compatible symbols mentioned in the episode: stocks (e.g. 'AAPL'), ETFs ('SPY'), indices ('^GSPC', '^TNX'), crypto ('BTC-USD'), forex ('EURUSD=X').",
    )
    macro_indicators: list[str] = Field(
        default_factory=list,
        description="FRED series IDs for macroeconomic concepts the episode discusses (e.g. 'CPIAUCSL' for CPI, 'DGS10' for 10Y yield, 'UNRATE' for unemployment, 'GDP' for GDP).",
    )
    named_entities: list[str] = Field(
        default_factory=list,
        description="People, events, companies, places, or concepts notable enough for a Wikipedia lookup. Plain names, not URLs.",
    )
    socratic_hooks: list[str] = Field(
        default_factory=list,
        description="3 questions the host raises but never fully resolves in the episode. Open threads worth following up on.",
    )


class BriefFinal(BaseModel):
    """Pass-2 output. Selected/sharpened fields, plus headline."""

    headline: str
    tldr: str
    thesis: str
    why_it_matters: list[str]
    pull_quotes: list[Quote] = Field(default_factory=list, max_length=5)
    by_the_numbers: list[DataPoint] = Field(default_factory=list)
    hosts: list[str] = Field(default_factory=list)
    guests: list[str] = Field(default_factory=list)
    resources_mentioned: list[Resource] = Field(default_factory=list)
    predictions: list[str] = Field(default_factory=list)
    counterpoints: list[str] = Field(default_factory=list)
    action_items: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    go_deeper: list[str] = Field(default_factory=list)
    visual_caption: str | None = None
    market_entities: list[str] = Field(default_factory=list)
    macro_indicators: list[str] = Field(default_factory=list)
    named_entities: list[str] = Field(default_factory=list)
    socratic_hooks: list[str] = Field(default_factory=list)
    language: str = Field(default="en", description="ISO 639-1 language code from Whisper transcript detection.")


class RenderInput(BaseModel):
    """Everything the renderer needs."""

    brief: BriefFinal
    show_name: str
    episode_title: str
    runtime: str
    pub_date: str | None
    source_url: str | None
    artwork_png: bytes | None = None
    suggestions: list[dict] = Field(default_factory=list)
    generated_at: str
    enrichment: object | None = None  # EnrichmentResult — opaque to pydantic

    model_config = {"arbitrary_types_allowed": True}
