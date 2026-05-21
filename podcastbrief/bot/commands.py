"""Telegram slash-command suite.

Each command uses the EXISTING BM25 retrieval (RagBot/ObsidianIndex) for vault
lookups and the EXISTING enricher adapters for live data. We never re-implement
retrieval here. Gemma 4 grounds each generated response strictly in the
returned context.

Categories:
- Active recall: /quiz, /flashcard, /retention
- Socratic:      /socratic, /debate, /challenge, /connect
- Reference:     /find, /numbers, /contradictions, /define
- Meta:          /news, /topics, /gaps, /help

Quiz answers are appended to {notes_dir}/.retention.json so /retention can
show overall accuracy, weakest concepts, and a streak.
"""
from __future__ import annotations
import asyncio
import io
import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import frontmatter

from podcastbrief.adapters.rss_news_enricher import RSSNewsEnricher
from podcastbrief.adapters.wikipedia_enricher import WikipediaEnricher
from podcastbrief.bot.index import ObsidianIndex
from podcastbrief.bot.rag import RagBot
from podcastbrief.briefing.extractor import language_directive
from podcastbrief.ports.llm import LLM

log = logging.getLogger(__name__)


# ---------------------- shared state ----------------------

@dataclass
class _PendingQuiz:
    """In-memory state for an active /quiz session per user."""
    questions: list[dict]
    asked_at: str
    topic: str = ""
    answers_correct: list[bool] = field(default_factory=list)


@dataclass
class _PendingFlashcard:
    claim: str
    correct: bool   # whether the claim is True or False
    explanation: str
    asked_at: str


@dataclass
class CommandContext:
    """One per running bot. Holds everything commands need."""
    llm: LLM
    rag: RagBot
    index: ObsidianIndex
    notes_dir: Path
    wiki: WikipediaEnricher
    rss: RSSNewsEnricher
    # Per-user mutable state.
    socratic: dict[str, bool] = field(default_factory=dict)
    quiz_pending: dict[str, _PendingQuiz] = field(default_factory=dict)
    flashcard_pending: dict[str, _PendingFlashcard] = field(default_factory=dict)


# ---------------------- retention log ----------------------

def _retention_path(notes_dir: Path) -> Path:
    return notes_dir / ".retention.json"


def _load_retention(notes_dir: Path) -> list[dict]:
    p = _retention_path(notes_dir)
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []


def _append_retention(notes_dir: Path, entry: dict) -> None:
    data = _load_retention(notes_dir)
    data.append(entry)
    try:
        _retention_path(notes_dir).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        log.warning("Retention write failed: %s", e)


# ---------------------- helpers ----------------------

def _latest_note_body(index: ObsidianIndex) -> tuple[str, str, str]:
    """Returns (file_stem, language, body without transcript). ('','en','') if vault is empty."""
    entries = index.all_entries()
    if not entries:
        return "", "en", ""
    e = entries[0]
    path = index.base_dir / f"{e.file_stem}.md"
    if not path.exists():
        return e.file_stem, "en", ""
    post = frontmatter.load(str(path))
    body = post.content or ""
    idx = body.find("\n## Transcript")
    if idx != -1:
        body = body[:idx].rstrip()
    lang = str((post.metadata or {}).get("language") or "en").split("-")[0].lower()
    return e.file_stem, lang, body


def _topic_vault_context(index: ObsidianIndex, topic: str, k: int = 6) -> str:
    """Pull body excerpts of vault notes most relevant to a topic."""
    hits = index.pick_relevant(topic, k=k)
    if not hits:
        return ""
    chunks: list[str] = []
    for entry, body in hits:
        chunks.append(f"[[{entry.file_stem}]] {entry.title} ({entry.date})\n{body}")
    return "\n\n---\n\n".join(chunks)


def _wrap(text: str, *, max_chars: int = 3800) -> list[str]:
    """Telegram cap is 4096 chars per message. Split long replies on paragraph boundaries."""
    if len(text) <= max_chars:
        return [text]
    out: list[str] = []
    buf: list[str] = []
    cur = 0
    for para in text.split("\n\n"):
        if cur + len(para) + 2 > max_chars and buf:
            out.append("\n\n".join(buf))
            buf, cur = [para], len(para)
        else:
            buf.append(para)
            cur += len(para) + 2
    if buf:
        out.append("\n\n".join(buf))
    return out


# ---------------------- ACTIVE RECALL ----------------------

_QUIZ_SYS = """You write conceptual multiple-choice questions to test podcast comprehension.

Produce EXACTLY 3 questions as a JSON object like:
{"questions":[{"q":"...","choices":["A) ...","B) ...","C) ...","D) ..."],"correct":"B","explanation":"...","timestamp":"24:18"}]}

Rules:
- Each question targets a non-trivial concept, not surface trivia.
- Exactly 4 choices labeled A) B) C) D).
- `correct` is the single letter.
- `explanation` is one sentence with a transcript-grounded reason.
- `timestamp` is MM:SS pulled from the source context if available, else "".
- All content must come from the provided context. Do not fabricate."""


async def cmd_quiz(ctx: CommandContext, user_id: str, args: list[str]) -> list[str]:
    topic = " ".join(args).strip()
    if topic:
        context = _topic_vault_context(ctx.index, topic, k=6)
        label = f"topic={topic!r}"
    else:
        stem, _, body = _latest_note_body(ctx.index)
        context = body
        label = f"latest brief ({stem})"
    if not context:
        return ["Nothing in the vault to quiz on yet."]
    raw = await _gemma_json(
        ctx.llm,
        system=_QUIZ_SYS,
        user=f"CONTEXT ({label}):\n{context}",
    )
    questions = raw.get("questions") or []
    if not questions:
        return ["Couldn't generate a quiz from the current vault content."]
    ctx.quiz_pending[user_id] = _PendingQuiz(
        questions=questions,
        asked_at=datetime.now(timezone.utc).isoformat(),
        topic=topic,
    )
    out: list[str] = ["🧠 Quiz time. Reply with A / B / C / D for each."]
    for i, q in enumerate(questions, 1):
        choices = "\n".join(q.get("choices", []))
        out.append(f"Q{i}. {q.get('q','')}\n{choices}")
    return out


def handle_quiz_answer(ctx: CommandContext, user_id: str, text: str) -> str | None:
    """Called from on_text when a user has a pending quiz and replies with letters."""
    pending = ctx.quiz_pending.get(user_id)
    if not pending:
        return None
    answers = re.findall(r"[A-Da-d]", text)
    if not answers:
        return None
    qs = pending.questions
    score = 0
    feedback: list[str] = []
    for i, q in enumerate(qs):
        if i >= len(answers):
            break
        guess = answers[i].upper()
        correct = (q.get("correct") or "").upper()
        ok = guess == correct
        if ok:
            score += 1
        pending.answers_correct.append(ok)
        ts = f" @ {q.get('timestamp')}" if q.get("timestamp") else ""
        mark = "✅" if ok else "❌"
        feedback.append(
            f"{mark} Q{i+1}: correct={correct}. {q.get('explanation','')}{ts}"
        )
        _append_retention(
            ctx.notes_dir,
            {
                "kind": "quiz",
                "user_id": user_id,
                "topic": pending.topic,
                "asked_at": pending.asked_at,
                "answered_at": datetime.now(timezone.utc).isoformat(),
                "question": q.get("q", ""),
                "correct": correct,
                "guess": guess,
                "is_correct": ok,
            },
        )
    del ctx.quiz_pending[user_id]
    summary = f"Score: {score}/{len(qs)}.\n\n" + "\n".join(feedback)
    return summary


_FLASH_SYS = """You produce one TRUE-or-FALSE flashcard about a podcast episode's content.

Return JSON: {"claim":"...","correct":true|false,"explanation":"...","timestamp":"MM:SS"}.

`claim` should be a confident statement (NOT a question) that is either accurate or subtly wrong.
`correct` is true if the claim is accurate.
`explanation` is one sentence grounded in the context.
`timestamp` is MM:SS from the source if available."""


async def cmd_flashcard(ctx: CommandContext, user_id: str, args: list[str]) -> list[str]:
    stem, _, body = _latest_note_body(ctx.index)
    if not body:
        return ["No briefs yet."]
    raw = await _gemma_json(ctx.llm, system=_FLASH_SYS, user=f"CONTEXT ({stem}):\n{body}")
    claim = str(raw.get("claim", "")).strip()
    if not claim:
        return ["Couldn't draft a flashcard right now."]
    ctx.flashcard_pending[user_id] = _PendingFlashcard(
        claim=claim,
        correct=bool(raw.get("correct", False)),
        explanation=str(raw.get("explanation", "")),
        asked_at=datetime.now(timezone.utc).isoformat(),
    )
    return [f"🎴 True or False?\n\n{claim}\n\nReply 'true' or 'false'."]


def handle_flashcard_answer(ctx: CommandContext, user_id: str, text: str) -> str | None:
    pending = ctx.flashcard_pending.get(user_id)
    if not pending:
        return None
    t = text.strip().lower()
    if t not in {"true", "false", "t", "f", "yes", "no"}:
        return None
    guess = t in {"true", "t", "yes"}
    ok = guess == pending.correct
    del ctx.flashcard_pending[user_id]
    _append_retention(
        ctx.notes_dir,
        {
            "kind": "flashcard",
            "user_id": user_id,
            "asked_at": pending.asked_at,
            "answered_at": datetime.now(timezone.utc).isoformat(),
            "claim": pending.claim,
            "correct": pending.correct,
            "guess": guess,
            "is_correct": ok,
        },
    )
    mark = "✅" if ok else "❌"
    return f"{mark} Correct answer: {pending.correct}. {pending.explanation}"


async def cmd_retention(ctx: CommandContext, user_id: str, args: list[str]) -> list[str]:
    data = [d for d in _load_retention(ctx.notes_dir) if d.get("user_id") == user_id]
    if not data:
        return ["No quiz history yet. Try /quiz or /flashcard."]
    total = len(data)
    correct = sum(1 for d in data if d.get("is_correct"))
    pct = correct / total * 100
    # Streak: count consecutive correct from end.
    streak = 0
    for d in reversed(data):
        if d.get("is_correct"):
            streak += 1
        else:
            break
    # Weak topics by quiz q text — keyword frequency among wrong answers.
    wrong_terms: dict[str, int] = {}
    for d in data:
        if d.get("is_correct"):
            continue
        for tok in re.findall(r"[A-Za-z]{4,}", str(d.get("question") or d.get("claim") or "")):
            t = tok.lower()
            wrong_terms[t] = wrong_terms.get(t, 0) + 1
    weakest = sorted(wrong_terms.items(), key=lambda kv: kv[1], reverse=True)[:5]
    lines = [
        f"📈 Retention",
        f"Total attempts: {total}",
        f"Accuracy: {pct:.0f}% ({correct}/{total})",
        f"Current correct streak: {streak}",
    ]
    if weakest:
        lines.append("Weakest concepts (from wrong answers): " + ", ".join(w for w, _ in weakest))
    return ["\n".join(lines)]


# ---------------------- SOCRATIC ----------------------

async def cmd_socratic(ctx: CommandContext, user_id: str, args: list[str]) -> list[str]:
    arg = (args[0] if args else "").lower()
    if arg in {"on", "enable", "yes"}:
        ctx.socratic[user_id] = True
        return ["Socratic mode ON. Every reply ends with a follow-up question."]
    if arg in {"off", "disable", "no"}:
        ctx.socratic[user_id] = False
        return ["Socratic mode OFF."]
    state = ctx.socratic.get(user_id, False)
    return [f"Socratic mode is {'ON' if state else 'OFF'}. Use /socratic on or /socratic off."]


async def cmd_moments(ctx: CommandContext, user_id: str, args: list[str]) -> list[str]:
    """Text-only sibling of /debate — return contrasting transcript quotes."""
    topic = " ".join(args).strip()
    if not topic:
        return ["Usage: /moments <topic>"]
    from podcastbrief.adapters.gemma_debate_retriever import GemmaDebateRetriever

    retriever = GemmaDebateRetriever(llm=ctx.llm)
    try:
        moments = await asyncio.to_thread(
            retriever.find_topic_moments, topic, ctx.notes_dir, 4,
        )
    except Exception as e:
        log.exception("/moments retrieval failed: %s", e)
        return [f"/moments failed: {e}"]

    if not moments:
        return [
            f"Not enough episodes cover '{topic}' yet. "
            "Add more podcasts on this subject (or run `podcastbrief reindex-timestamps`) "
            "and try again."
        ]

    lines = [f"🔍 Moments on '{topic}':"]
    for m in moments:
        ts = f"{int(m.start_seconds // 60):02d}:{int(m.start_seconds % 60):02d}"
        lines.append(
            f"\n[[{m.episode_slug}]] — {m.host_name} @ `{ts}` ({m.stance})\n"
            f"> {m.transcript_text.strip()}"
        )
    return _wrap("\n".join(lines))


_CHALLENGE_SYS = """You identify the weakest argument in a podcast episode and prompt the user to defend or attack it.

Output:
1. Quote or paraphrase the weak argument from the episode.
2. Explain in one sentence why it's the weakest.
3. Ask: "Defend it or attack it?"

Grounded strictly in the provided brief content. No markdown headers."""


async def cmd_challenge(ctx: CommandContext, user_id: str, args: list[str]) -> list[str]:
    stem, lang, body = _latest_note_body(ctx.index)
    if not body:
        return ["No briefs yet."]
    out = await _gemma_text(
        ctx.llm,
        system=_CHALLENGE_SYS + language_directive(lang),
        user=f"BRIEF ({stem}):\n{body}",
    )
    return _wrap(out)


_CONNECT_SYS = """You synthesize how today's episode relates to a topic the user has explored before in their podcast vault.

Output (no headers): 3-4 short paragraphs comparing today's brief to the older briefs on the topic. Cite which brief by short filename like [[2026-05-10_The-5-Biggest-Investing-Mistakes]] when you reference one. Highlight agreements, divergences, and any net-new claims.

Grounded strictly in the provided vault context."""


async def cmd_connect(ctx: CommandContext, user_id: str, args: list[str]) -> list[str]:
    topic = " ".join(args).strip()
    if not topic:
        return ["Usage: /connect <topic>"]
    stem_today, lang, body_today = _latest_note_body(ctx.index)
    related = _topic_vault_context(ctx.index, topic, k=6)
    out = await _gemma_text(
        ctx.llm,
        system=_CONNECT_SYS + language_directive(lang),
        user=(
            f"TODAY'S BRIEF ({stem_today}):\n{body_today}\n\n"
            f"OLDER VAULT NOTES ABOUT {topic!r}:\n{related}"
        ),
    )
    return _wrap(out)


# ---------------------- REFERENCE ----------------------

async def cmd_find(ctx: CommandContext, user_id: str, args: list[str]) -> list[str]:
    concept = " ".join(args).strip()
    if not concept:
        return ["Usage: /find <concept>"]
    hits = ctx.index.pick_relevant(concept, k=20)
    if not hits:
        return [f"No mentions of '{concept}' in the vault yet."]
    out: list[str] = [f"🔎 Mentions of '{concept}':"]
    pat = re.compile(re.escape(concept), re.IGNORECASE)
    for entry, body in hits[:8]:
        matches: list[str] = []
        for line in body.splitlines():
            if pat.search(line):
                # Pull a timestamp if there's one on the line above (quotes carry MM:SS).
                ts = ""
                m = re.search(r"`(\d{1,2}:\d{2})`", line)
                if m:
                    ts = f" `{m.group(1)}`"
                trimmed = re.sub(r"`\d{1,2}:\d{2}`", "", line).strip("> ").strip()
                if trimmed:
                    matches.append(f"  • {trimmed[:160]}{ts}")
                if len(matches) >= 3:
                    break
        if matches:
            out.append(f"\n[[{entry.file_stem}]] ({entry.date}):")
            out.extend(matches)
    return _wrap("\n".join(out))


_NUMBERS_SYS = """Return the figures, stats, percentages, dollar amounts, and dates from the provided brief content, structured as a clean numbered list. No commentary, no headers. One number per line: figure — what it measures — short context."""


async def cmd_numbers(ctx: CommandContext, user_id: str, args: list[str]) -> list[str]:
    stem, lang, body = _latest_note_body(ctx.index)
    if not body:
        return ["No briefs yet."]
    out = await _gemma_text(
        ctx.llm,
        system=_NUMBERS_SYS + language_directive(lang),
        user=f"BRIEF ({stem}):\n{body}",
    )
    return _wrap(out)


_CONTRA_SYS = """You identify points where today's brief contradicts older briefs on the same topic.

Output: 1-4 short paragraphs. For each contradiction: name the claim today, cite the older brief that says otherwise with [[file_stem]], and note what changed.

If there are no contradictions, say "No contradictions found across the vault." and stop."""


async def cmd_contradictions(ctx: CommandContext, user_id: str, args: list[str]) -> list[str]:
    stem_today, lang, body_today = _latest_note_body(ctx.index)
    if not body_today:
        return ["No briefs yet."]
    related = _topic_vault_context(ctx.index, stem_today, k=8)
    out = await _gemma_text(
        ctx.llm,
        system=_CONTRA_SYS + language_directive(lang),
        user=f"TODAY'S BRIEF:\n{body_today}\n\nOLDER NOTES:\n{related}",
    )
    return _wrap(out)


async def cmd_define(ctx: CommandContext, user_id: str, args: list[str]) -> list[str]:
    concept = " ".join(args).strip()
    if not concept:
        return ["Usage: /define <concept>"]
    result = await ctx.wiki.enrich(named_entities=[concept])
    if not result.wiki:
        return [f"Wikipedia had no entry for '{concept}'."]
    w = result.wiki[0]
    return [f"📖 {w.name}\n\n{w.summary}\n\n{w.url}"]


# ---------------------- META ----------------------

async def cmd_news(ctx: CommandContext, user_id: str, args: list[str]) -> list[str]:
    topic = " ".join(args).strip()
    if not topic:
        return ["Usage: /news <topic>"]
    result = await ctx.rss.enrich(named_entities=topic.split())
    if not result.news:
        return [f"No recent headlines for '{topic}' in the past 7 days."]
    out: list[str] = [f"📰 Top recent headlines for '{topic}':"]
    for n in result.news:
        date = f" · {n.pub_date}" if n.pub_date else ""
        out.append(f"\n[{n.source}{date}] {n.title}\n{n.url}\n{n.summary}")
    return _wrap("\n".join(out))


async def cmd_topics(ctx: CommandContext, user_id: str, args: list[str]) -> list[str]:
    entries = ctx.index.all_entries()
    if not entries:
        return ["No briefs yet."]
    from collections import Counter
    now = datetime.now(timezone.utc)
    week_counter: Counter[str] = Counter()
    month_counter: Counter[str] = Counter()
    for e in entries:
        try:
            d = datetime.fromisoformat(e.date.split("T")[0])
            d = d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d
        except Exception:
            d = None
        for t in e.topics:
            if d is not None and (now - d).days <= 7:
                week_counter[t] += 1
            if d is not None and (now - d).days <= 31:
                month_counter[t] += 1
            # If date unparseable, count toward month at least.
            if d is None:
                month_counter[t] += 1
    lines = ["🗂 Recurring themes"]
    if week_counter:
        top = ", ".join(f"{t} ({n})" for t, n in week_counter.most_common(5))
        lines.append(f"This week: {top}")
    if month_counter:
        top = ", ".join(f"{t} ({n})" for t, n in month_counter.most_common(5))
        lines.append(f"This month: {top}")
    return ["\n".join(lines)]


async def cmd_gaps(ctx: CommandContext, user_id: str, args: list[str]) -> list[str]:
    """Pull socratic_hooks from recent briefs' frontmatter / sections."""
    entries = ctx.index.all_entries()
    if not entries:
        return ["No briefs yet."]
    out: list[str] = ["⚠️ Open threads — questions hosts raised but didn't resolve:"]
    found = False
    for e in entries[:10]:
        path = ctx.index.base_dir / f"{e.file_stem}.md"
        if not path.exists():
            continue
        post = frontmatter.load(str(path))
        hooks = list((post.metadata or {}).get("socratic_hooks") or [])
        if not hooks:
            # Also try to pull from a section in the markdown body in case the
            # writer dumped them into the rendered note.
            body = post.content or ""
            m = re.search(r"## (?:Open Threads|Socratic Hooks|Gaps)\n(.+?)(?=\n## |\Z)", body, re.S)
            if m:
                hooks = [ln.lstrip("- *• ").strip() for ln in m.group(1).splitlines() if ln.strip()]
        if hooks:
            found = True
            out.append(f"\n[[{e.file_stem}]]")
            out.extend(f"  ❓ {h}" for h in hooks[:3])
    if not found:
        return ["No socratic_hooks recorded yet — new episodes will populate this."]
    return _wrap("\n".join(out))


_HELP = """🤖 podcastbrief command list

ACTIVE RECALL
/quiz — 3 MCQs from today's brief
/quiz <topic> — cross-episode quiz on a topic
/flashcard — one true/false claim, with explanation
/retention — your quiz score history

SOCRATIC
/socratic on|off — toggle follow-up questions on every answer
/debate <topic> — voice note: 3-4 contrasting host clips + analysis (audio)
/moments <topic> — text-only: 3-4 contrasting host quotes from across the vault
/challenge — pick the weakest episode argument, defend or attack
/connect <topic> — how today's episode relates to older briefs

REFERENCE
/find <concept> — every vault mention with timestamps
/numbers — all stats from today's brief
/contradictions — points where today contradicts older briefs
/define <concept> — Wikipedia summary

META
/news <topic> — top 3 recent headlines (past 7 days)
/topics — recurring themes this week / month
/gaps — open questions hosts raised but didn't resolve
/run — reprocess the most-recently-added playlist episode
/help — this list"""


async def cmd_help(ctx: CommandContext, user_id: str, args: list[str]) -> list[str]:
    return [_HELP]


# ---------------------- LLM helpers ----------------------

async def _gemma_text(llm: LLM, *, system: str, user: str, temperature: float = 0.4) -> str:
    """Run a Gemma text completion off the event loop."""
    return await asyncio.to_thread(
        llm.complete, system=system, user=user, temperature=temperature
    )


async def _gemma_json(llm: LLM, *, system: str, user: str) -> dict:
    """Run a generic JSON completion. Returns {} on failure."""
    try:
        # Direct chat call via the underlying ollama client for free-form JSON.
        # We don't have a typed schema here — pass an empty pydantic shell.
        from pydantic import BaseModel as _BM
        class _Free(_BM):
            class Config:
                extra = "allow"
        result = await asyncio.to_thread(
            llm.json_complete,
            system=system,
            user=user,
            schema=_Free,
            example='{"questions":[]}',
        )
        return result.model_dump()
    except Exception as e:
        log.warning("Free JSON completion failed: %s", e)
        return {}


# ---------------------- dispatch ----------------------

COMMANDS = {
    "quiz": cmd_quiz,
    "flashcard": cmd_flashcard,
    "retention": cmd_retention,
    "socratic": cmd_socratic,
    "challenge": cmd_challenge,
    "connect": cmd_connect,
    "find": cmd_find,
    "moments": cmd_moments,
    "numbers": cmd_numbers,
    "contradictions": cmd_contradictions,
    "define": cmd_define,
    "news": cmd_news,
    "topics": cmd_topics,
    "gaps": cmd_gaps,
    "help": cmd_help,
}
