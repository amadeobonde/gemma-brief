from __future__ import annotations
import logging
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from podcastbrief.bot.index import IndexEntry, ObsidianIndex
from podcastbrief.ports.llm import LLM

log = logging.getLogger(__name__)


SYSTEM_RAG = """You are Jason, the Podcast Librarian — a knowledgeable assistant who helps users navigate their podcast brief vault.

You receive:
1. INDEX.md — the always-loaded one-line catalogue of every brief.
2. RETRIEVED EXCERPTS — full body content of relevant briefs (TL;DR, Thesis, Why It Matters, Key Quotes, By The Numbers, Predictions, Counterpoints, Resources, Action Items, Similar Episodes). When excerpts are present, USE THEM to answer.
3. The user's last few messages.

ANSWER STYLE
- Default to short, mobile-friendly answers (2-5 sentences).
- BUT when the user asks for quotes, numbers, takeaways, predictions, action items, resources, or "give me X items" — return the actual content from the retrieved excerpts. A list of 3 quotes should be 3 quotes, not a refusal.
- Use line breaks for readability. Plain text, no markdown headers, no bullet asterisks; numbered lists "1.", "2.", "3." are fine.
- When you reference a brief by name, include its [[file_stem]] wikilink so the user can open it in Obsidian.

WHAT TO DO BY QUESTION TYPE
- "give me N quotes" / "best quotes" / "top quotes" → pull from the brief's "## Key Quotes" section. Each quote: the verbatim text, the speaker, and the timestamp if present. If only fewer than N exist, return what's there.
- "what numbers" / "stats" / "data" → pull from "## By The Numbers".
- "what's the takeaway" / "why does it matter" → pull from "## TL;DR" + "## Why It Matters".
- "what shows do I have" / "list briefs" → use INDEX.md.
- "predictions" / "what did they predict" → pull from "## Predictions".
- "resources" / "books mentioned" / "tools" → pull from "## Resources Mentioned".

NEVER
- Don't say "I don't have the full transcript" if the retrieved excerpts contain the answer — the excerpts are full brief content (without raw transcript), and that's enough.
- Don't fabricate titles, episode names, dates, URLs, or quotes that aren't in the provided context.
- Don't apologize when you have data — just answer.

If genuinely no relevant content: "Don't see anything about that in the briefs yet. What else can I help with?"
"""


@dataclass
class _UserMemory:
    turns: deque = field(default_factory=lambda: deque(maxlen=10))


class RagBot:
    """Index-pick-load RAG over an Obsidian-style markdown vault.

    Always-loaded: INDEX.md (cheap, small, structured).
    Query-time:    BM25 over note bodies → top-K excerpts loaded just-in-time.
    """

    def __init__(self, *, llm: LLM, notes_dir: Path) -> None:
        self.llm = llm
        self.index = ObsidianIndex(base_dir=notes_dir)
        self._user_memory: dict[str, _UserMemory] = {}

    def answer(self, *, user_id: str, question: str) -> str:
        mem = self._user_memory.setdefault(user_id, _UserMemory())
        mem.turns.append(("user", question))

        index_text = self.index.index_text()
        relevant = self.index.pick_relevant(question, k=4)
        excerpts = self._format_excerpts(relevant)
        history = self._format_history(mem)

        user_msg = (
            f"INDEX.md (always loaded):\n{index_text}\n\n"
            f"RETRIEVED EXCERPTS:\n{excerpts or '(none — let the index guide you)'}\n\n"
            f"RECENT CONVERSATION:\n{history}\n\n"
            f"USER QUESTION:\n{question}"
        )

        try:
            answer = self.llm.complete(
                system=SYSTEM_RAG, user=user_msg, temperature=0.4
            ).strip()
        except Exception as e:
            log.exception("RAG completion failed: %s", e)
            answer = "Hit an issue answering that. Try again in a sec."

        mem.turns.append(("assistant", answer))
        return answer

    @staticmethod
    def _format_excerpts(items: list[tuple[IndexEntry, str]]) -> str:
        if not items:
            return ""
        chunks: list[str] = []
        for entry, excerpt in items:
            chunks.append(
                f"[[{entry.file_stem}]] {entry.title} — {entry.show} ({entry.date})\n"
                f"{excerpt}\n"
            )
        return "\n---\n".join(chunks)

    @staticmethod
    def _format_history(mem: _UserMemory) -> str:
        if not mem.turns:
            return "(none)"
        lines = []
        # Drop the just-appended current question from history view
        for role, text in list(mem.turns)[:-1]:
            lines.append(f"{role.upper()}: {text}")
        return "\n".join(lines) or "(none)"
