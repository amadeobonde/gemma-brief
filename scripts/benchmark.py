"""Benchmark: single-shot vs two-pass summarization across the last N briefs.

For every episode currently in the vault, we already have a transcript cached
(./.transcript_cache/{sha}.json keyed by audio bytes hash). For each one:

  1. Generate a "single-shot" summary — one Gemma 4 call producing a TL;DR +
     bullets + quotes + numbers in one go, no second pass.
  2. Use the existing two-pass output (the vault's Pass 2 BriefFinal that's
     already saved in the markdown note's frontmatter / body).
  3. Have Gemma 4 score both on three 1-10 criteria:
        - claim_accuracy   (do claims actually match transcript?)
        - quote_relevance  (are the chosen quotes the strongest?)
        - actionability    (would a reader actually do something differently?)

Prints a markdown table and writes ./benchmarks/results.md.

Usage:
  python scripts/benchmark.py
  python scripts/benchmark.py --limit 5         # only run on 5 episodes
  python scripts/benchmark.py --episode <stem>  # run on a single brief
"""
from __future__ import annotations
import argparse
import json
import re
import statistics
import sys
import textwrap
import time
from pathlib import Path

import frontmatter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from podcastbrief.adapters.ollama_gemma import OllamaGemma
from podcastbrief.adapters.whisper_http import WhisperHttpTranscriber  # noqa: F401
from podcastbrief.core.config import load_settings


SINGLE_SHOT_SYSTEM = """You are summarizing a podcast transcript.

Produce a single-shot brief with these sections, in this order:
  TL;DR: one sentence.
  WHY IT MATTERS: 3-5 bullets (one line each).
  KEY QUOTES: 3 verbatim quotes with speaker.
  BY THE NUMBERS: 2-4 figures with what they measure.

No headers like ## or **. Plain text. No preamble."""


SCORE_SYSTEM = """You are a strict editor evaluating two podcast-brief summaries against the source transcript.

Score each summary on three criteria, integer 1-10:
  claim_accuracy   — do claims match the transcript? 10 = every claim grounded; 1 = mostly invented.
  quote_relevance  — are the chosen quotes the strongest/most representative? 10 = nails the key moments; 1 = trivial soundbites.
  actionability    — would a reader actually do something differently after reading? 10 = clear next steps; 1 = pure recap.

Return ONLY valid JSON:
{"single_shot":{"claim_accuracy":N,"quote_relevance":N,"actionability":N},"two_pass":{"claim_accuracy":N,"quote_relevance":N,"actionability":N}}"""


def _extract_brief_body(note_path: Path) -> str:
    """Return the human-readable brief body (headline through resources),
    excluding the transcript dump, for use as the two-pass summary."""
    post = frontmatter.load(str(note_path))
    body = post.content or ""
    idx = body.find("\n## Transcript")
    if idx != -1:
        body = body[:idx].rstrip()
    return body


def _extract_transcript_from_note(note_path: Path) -> str:
    """Pull the transcript dump section from a saved note so we don't need to
    re-load .transcript_cache files separately. The note already includes the
    first 10K chars of the transcript."""
    post = frontmatter.load(str(note_path))
    body = post.content or ""
    m = re.search(r"## Transcript\n```\n(.+?)\n```", body, re.S)
    if m:
        return m.group(1)
    return ""


def _single_shot(llm: OllamaGemma, *, transcript: str, show: str, title: str) -> str:
    user = (
        f"SHOW: {show}\nEPISODE: {title}\n\nTRANSCRIPT:\n{transcript[:80000]}"
    )
    return llm.complete(system=SINGLE_SHOT_SYSTEM, user=user, temperature=0.4).strip()


def _score(
    llm: OllamaGemma,
    *,
    transcript: str,
    single_shot: str,
    two_pass: str,
) -> dict:
    user = (
        f"TRANSCRIPT EXCERPT:\n{transcript[:40000]}\n\n"
        f"=== SINGLE-SHOT SUMMARY ===\n{single_shot}\n\n"
        f"=== TWO-PASS SUMMARY ===\n{two_pass}"
    )
    from pydantic import BaseModel as _BM, ConfigDict

    class _Free(_BM):
        model_config = ConfigDict(extra="allow")

    raw = llm.json_complete(
        system=SCORE_SYSTEM,
        user=user,
        schema=_Free,
        example='{"single_shot":{"claim_accuracy":7,"quote_relevance":6,"actionability":5},"two_pass":{"claim_accuracy":9,"quote_relevance":8,"actionability":7}}',
        temperature=0.1,
    )
    return raw.model_dump()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=10, help="Max episodes to benchmark.")
    ap.add_argument("--episode", help="Brief file_stem to score (skip the rest).")
    ap.add_argument("--out", default=str(ROOT / "benchmarks" / "results.md"))
    args = ap.parse_args()

    s = load_settings()
    notes_dir = Path(s.notes_dir)
    notes = sorted(notes_dir.glob("*.md"), reverse=True)
    notes = [p for p in notes if p.name not in {"INDEX.md"}]
    if args.episode:
        notes = [p for p in notes if p.stem == args.episode]
    notes = notes[: args.limit]
    if not notes:
        print("No briefs in the vault to benchmark.", file=sys.stderr)
        return

    llm = OllamaGemma(host=s.ollama_host, model=s.llm_model)

    rows: list[dict] = []
    for path in notes:
        post = frontmatter.load(str(path))
        meta = post.metadata or {}
        show = str(meta.get("show") or "")
        title = str(meta.get("title") or path.stem)
        transcript = _extract_transcript_from_note(path)
        if not transcript:
            print(f"  skip {path.stem}: no transcript in note", file=sys.stderr)
            continue
        two_pass = _extract_brief_body(path)
        print(f"  benchmarking {path.stem}…", file=sys.stderr)

        t0 = time.time()
        single = _single_shot(llm, transcript=transcript, show=show, title=title)
        single_dur = time.time() - t0
        try:
            scores = _score(llm, transcript=transcript, single_shot=single, two_pass=two_pass)
        except Exception as e:
            print(f"    score failed: {e}", file=sys.stderr)
            continue
        ss = scores.get("single_shot") or {}
        tp = scores.get("two_pass") or {}
        rows.append({
            "episode": path.stem,
            "single_shot": {k: int(ss.get(k, 0)) for k in ("claim_accuracy", "quote_relevance", "actionability")},
            "two_pass": {k: int(tp.get(k, 0)) for k in ("claim_accuracy", "quote_relevance", "actionability")},
            "single_shot_seconds": round(single_dur, 1),
        })

    if not rows:
        print("No rows produced.", file=sys.stderr)
        return

    # Markdown table.
    lines = [
        "# Benchmark — single-shot vs two-pass summarization",
        "",
        "Each summary is scored 1-10 by Gemma 4 on three criteria. Higher is better.",
        "",
        "| Episode | Single-Shot CA / QR / Act | Two-Pass CA / QR / Act | Δ (two-pass minus single-shot) |",
        "|---|---|---|---|",
    ]
    diffs: dict[str, list[int]] = {"claim_accuracy": [], "quote_relevance": [], "actionability": []}
    for r in rows:
        ss = r["single_shot"]
        tp = r["two_pass"]
        d = {k: tp[k] - ss[k] for k in diffs}
        for k in diffs:
            diffs[k].append(d[k])
        lines.append(
            f"| {r['episode']} | "
            f"{ss['claim_accuracy']} / {ss['quote_relevance']} / {ss['actionability']} | "
            f"{tp['claim_accuracy']} / {tp['quote_relevance']} / {tp['actionability']} | "
            f"{d['claim_accuracy']:+d} / {d['quote_relevance']:+d} / {d['actionability']:+d} |"
        )
    lines.append("")
    lines.append("## Aggregates")
    for k in diffs:
        avg = statistics.mean(diffs[k]) if diffs[k] else 0
        lines.append(f"- {k}: two-pass beats single-shot by {avg:+.2f} on average")
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\nWrote {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
