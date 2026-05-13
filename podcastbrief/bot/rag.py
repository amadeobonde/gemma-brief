from __future__ import annotations
import logging
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
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


SYSTEM_RAG_VOICE = """You are Jason, the user's podcast librarian, replying out loud on a phone call.

Your reply will be SPOKEN ALOUD to the user via text-to-speech. Write the way a friend who actually listened to the podcast would casually answer. NOT a recap, NOT a structured summary, NOT a list.

HARD RULES
- 30 to 55 words. Two or three short sentences. No more.
- One concrete answer. If they ask for "three quotes", pick the single most striking one and paraphrase it naturally in your own words — DO NOT list three.
- Conversational tone: contractions, normal punctuation, complete sentences. No headings, no bullets, no numbered lists, no quotation marks around speaker names, no markdown, no [[wikilinks]], no timestamps like "24:18", no URLs.
- Don't say "the brief" — say "this episode" or "they". Don't say "TL;DR".
- If they greet ("hi", "thanks"), reply with one casual line. Don't recap anything.
- If you don't have the info, say one short honest sentence ("I don't think that came up — what else?").

EXAMPLES
- User: "give me three quotes about compounding" → "There's a great line where the host basically says ninety-five percent of your account value ends up being pure growth, not what you put in. Pretty wild when you think about it."
- User: "what are the five mistakes?" → "Quick version: don't wait to start investing, secure your basics first like insurance and debt, skip the trendy speculative bets, max out tax-advantaged accounts, and don't let emotions drive your moves."
- User: "hi" → "Hey, what's up? Want me to pull anything from your latest brief?"
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

    def answer(
        self,
        *,
        user_id: str,
        question: str,
        mode: Literal["text", "voice"] = "text",
    ) -> str:
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

        if mode == "voice":
            system_prompt = SYSTEM_RAG_VOICE
            temperature = 0.6
            # Don't cap output tokens here — Gemma uses ~2 tokens per word and tight
            # caps just truncate mid-sentence. The system prompt enforces length,
            # and VoiceConfig.max_chars trims the TTS input as a hard safety net.
            num_predict = None
        else:
            system_prompt = SYSTEM_RAG
            temperature = 0.4
            num_predict = None

        try:
            answer = self.llm.complete(
                system=system_prompt,
                user=user_msg,
                temperature=temperature,
                num_predict=num_predict,
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
