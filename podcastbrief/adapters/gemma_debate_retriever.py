"""Two-stage debate retriever: BM25 pre-filter → Gemma 4 rerank.

Stage 1 takes the user's topic and asks the existing ObsidianIndex BM25 layer
for ~20 candidate Whisper segments from across the vault. Stage 2 hands those
to Gemma 4 with a strict prompt asking it to pick 3-4 contrasting clips, one
per episode max.
"""
from __future__ import annotations
import json
import logging
import re
from pathlib import Path
from typing import Sequence

import frontmatter
from pydantic import BaseModel, Field
from rank_bm25 import BM25Plus

from podcastbrief.bot.index import INDEX_FILENAME
from podcastbrief.core.vault import read_whisper_sidecar
from podcastbrief.ports.debate_retriever import Stance, TopicMoment
from podcastbrief.ports.llm import LLM

log = logging.getLogger(__name__)


# ---------------- BM25 over Whisper segments ----------------


def _tokenize(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) > 2]


class _Candidate(BaseModel):
    """Internal pre-rerank candidate. Trimmed before passing to Gemma."""
    episode_slug: str
    episode_title: str
    host_name: str
    segment_index: int
    start_seconds: float
    end_seconds: float
    text: str
    bm25_score: float


def _host_name_for(metadata: dict) -> str:
    """Best-effort host name from frontmatter. Falls back to show name.

    Future: parse a `host:` frontmatter key when episodes are tagged with it.
    """
    h = str(metadata.get("host") or "").strip()
    if h:
        return h
    return str(metadata.get("show") or "Unknown host").strip()


def _collect_segments(vault_path: Path) -> list[_Candidate]:
    """Walk the vault, pulling every Whisper sidecar segment as a candidate."""
    cands: list[_Candidate] = []
    for md_path in sorted(vault_path.glob("*.md")):
        if md_path.name == INDEX_FILENAME:
            continue
        try:
            post = frontmatter.load(str(md_path))
        except Exception:
            continue
        meta = dict(post.metadata or {})
        stem = md_path.stem
        sidecar = read_whisper_sidecar(vault_path, stem)
        if not sidecar:
            continue
        title = str(meta.get("title") or stem)
        host = _host_name_for(meta)
        for i, seg in enumerate(sidecar.get("segments") or []):
            text = (seg.get("text") or "").strip()
            if not text or len(text) < 20:
                continue
            cands.append(
                _Candidate(
                    episode_slug=stem,
                    episode_title=title,
                    host_name=host,
                    segment_index=i,
                    start_seconds=float(seg.get("start") or 0.0),
                    end_seconds=float(seg.get("end") or 0.0),
                    text=text,
                    bm25_score=0.0,
                )
            )
    return cands


def _bm25_rank(cands: list[_Candidate], topic: str, top_k: int = 20) -> list[_Candidate]:
    """Rank candidates by BM25Plus relevance to topic. Filters out true non-matches.

    BM25Plus avoids the negative-IDF / zero-score collapse vanilla BM25Okapi hits
    on small vaults (which is the common case at first install). A candidate is
    considered a match if at least one query token appears in its tokenized text.
    """
    if not cands:
        return []
    query_tokens = _tokenize(topic) or [topic.lower()]
    corpus = [_tokenize(c.text) or ["_"] for c in cands]
    bm25 = BM25Plus(corpus)
    scores = bm25.get_scores(query_tokens)
    qset = set(query_tokens)
    paired = []
    for cand, score, tokens in zip(cands, scores, corpus):
        # Require at least one query-token overlap to avoid false positives.
        if not (qset & set(tokens)):
            continue
        paired.append((cand, float(score)))
    paired.sort(key=lambda p: p[1], reverse=True)
    out: list[_Candidate] = []
    for cand, score in paired[:top_k]:
        out.append(cand.model_copy(update={"bm25_score": score}))
    return out


def _expand_to_sentence(
    candidate: _Candidate,
    sidecar: dict,
    *,
    max_extra_seconds: float = 6.0,
) -> _Candidate:
    """Extend `candidate.end` to the next sentence boundary using word timestamps.

    Whisper segments are ASR chunks, not sentences. The /debate spec wants
    "the end of the complete thought, not mid-sentence" — we look at adjacent
    segments and stop at the next `.`/`?`/`!`.
    """
    segments = sidecar.get("segments") or []
    end = candidate.end_seconds
    limit = candidate.end_seconds + max_extra_seconds
    for j in range(candidate.segment_index + 1, len(segments)):
        seg = segments[j]
        seg_start = float(seg.get("start") or 0.0)
        if seg_start > limit:
            break
        text = (seg.get("text") or "").strip()
        end = float(seg.get("end") or seg_start)
        if text and text[-1:] in ".!?":
            break
    return candidate.model_copy(update={"end_seconds": max(candidate.end_seconds, end)})


# ---------------- Gemma rerank ----------------


_RERANK_SYS = """You are selecting audio clips for a debate compilation.

Rules — non-negotiable:
1. Clips must take a clear, specific stance (not vague hedging).
2. Clips must contrast: different opinions, predictions, or reasoning.
3. Clips must be self-contained — a listener understands without context.
4. Pick clips from DIFFERENT episodes. Never two clips from the same episode.
5. Pick 3 or 4 clips total. If fewer than 2 valid clips exist, return an empty list.

For each selected clip, return:
- candidate_index (the integer index from the input list)
- start_seconds (use the candidate's start_seconds, or refine to the exact sentence start)
- end_seconds (use the candidate's end_seconds, or extend to the end of a complete thought)
- stance (one of: bullish, bearish, neutral, uncertain)
- why_selected (one sentence — be specific)

Return ONLY valid JSON matching the schema. No preamble, no markdown."""


class _PickedClip(BaseModel):
    candidate_index: int
    start_seconds: float
    end_seconds: float
    stance: Stance = "neutral"
    why_selected: str = ""


class _RerankResult(BaseModel):
    picks: list[_PickedClip] = Field(default_factory=list)


def _build_rerank_user(topic: str, cands: Sequence[_Candidate]) -> str:
    items = []
    for i, c in enumerate(cands):
        items.append({
            "index": i,
            "episode_slug": c.episode_slug,
            "episode_title": c.episode_title,
            "host_name": c.host_name,
            "start_seconds": round(c.start_seconds, 2),
            "end_seconds": round(c.end_seconds, 2),
            "text": c.text,
        })
    return (
        f"Topic: {topic}\n\n"
        f"Candidate moments (use `index` to refer back to them):\n"
        f"{json.dumps(items, ensure_ascii=False)}"
    )


# ---------------- adapter ----------------


class GemmaDebateRetriever:
    """Two-stage retriever: BM25 candidate pool → Gemma 4 rerank.

    The vault must contain Whisper sidecars (`{stem}_whisper.json`) — run
    `podcastbrief reindex-timestamps` first for episodes ingested without them.
    """

    def __init__(self, *, llm: LLM, candidate_pool: int = 20) -> None:
        self.llm = llm
        self.candidate_pool = candidate_pool

    def find_topic_moments(
        self,
        topic: str,
        vault_path: Path,
        max_clips: int = 4,
    ) -> list[TopicMoment]:
        if max_clips < 2:
            raise ValueError("max_clips must be >= 2 for a debate compilation")

        cands_all = _collect_segments(Path(vault_path))
        if not cands_all:
            log.info("DebateRetriever: vault has no Whisper sidecars yet.")
            return []

        cands = _bm25_rank(cands_all, topic, top_k=self.candidate_pool)
        if len(cands) < 2:
            log.info("DebateRetriever: only %d BM25 hits — not enough to debate.", len(cands))
            return []

        try:
            picks = self.llm.json_complete(
                system=_RERANK_SYS,
                user=_build_rerank_user(topic, cands),
                schema=_RerankResult,
                temperature=0.2,
                max_retries=1,
            ).picks
        except Exception as e:
            log.warning("Gemma rerank failed (%s); falling back to BM25 top picks.", e)
            picks = _fallback_picks(cands, max_clips)

        moments = _picks_to_moments(picks, cands, vault_path, max_clips)
        log.info("DebateRetriever: returning %d moment(s) for %r", len(moments), topic)
        return moments


def _fallback_picks(cands: list[_Candidate], max_clips: int) -> list[_PickedClip]:
    """When Gemma fails, take the top BM25 hits with cross-episode dedup."""
    seen_episodes: set[str] = set()
    out: list[_PickedClip] = []
    for i, c in enumerate(cands):
        if c.episode_slug in seen_episodes:
            continue
        seen_episodes.add(c.episode_slug)
        out.append(_PickedClip(
            candidate_index=i,
            start_seconds=c.start_seconds,
            end_seconds=c.end_seconds,
            stance="neutral",
            why_selected="BM25 fallback (Gemma rerank unavailable)",
        ))
        if len(out) >= max_clips:
            break
    return out


def _picks_to_moments(
    picks: list[_PickedClip],
    cands: list[_Candidate],
    vault_path: Path,
    max_clips: int,
) -> list[TopicMoment]:
    """Validate Gemma's picks, dedup episodes, expand to sentence boundaries."""
    moments: list[TopicMoment] = []
    seen_episodes: set[str] = set()
    sidecar_cache: dict[str, dict | None] = {}

    for p in picks:
        if p.candidate_index < 0 or p.candidate_index >= len(cands):
            continue
        c = cands[p.candidate_index]
        if c.episode_slug in seen_episodes:
            continue
        sidecar = sidecar_cache.get(c.episode_slug)
        if sidecar is None:
            sidecar = read_whisper_sidecar(vault_path, c.episode_slug) or {}
            sidecar_cache[c.episode_slug] = sidecar
        # Use Gemma's timestamps but expand to sentence boundary.
        adjusted = c.model_copy(update={
            "start_seconds": max(0.0, float(p.start_seconds)),
            "end_seconds": max(float(p.start_seconds) + 1.0, float(p.end_seconds)),
        })
        adjusted = _expand_to_sentence(adjusted, sidecar)
        seen_episodes.add(c.episode_slug)
        try:
            moments.append(TopicMoment(
                episode_slug=c.episode_slug,
                episode_title=c.episode_title,
                host_name=c.host_name,
                start_seconds=adjusted.start_seconds,
                end_seconds=adjusted.end_seconds,
                transcript_text=c.text,
                relevance_score=c.bm25_score,
                stance=p.stance,
            ))
        except Exception as e:
            log.warning("Skipping invalid TopicMoment for %s: %s", c.episode_slug, e)
        if len(moments) >= max_clips:
            break

    return moments
