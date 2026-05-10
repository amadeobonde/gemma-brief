from __future__ import annotations
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import frontmatter
from rank_bm25 import BM25Okapi

log = logging.getLogger(__name__)


INDEX_FILENAME = "INDEX.md"
INDEX_HEADER = "# Podcast Brief Index\n\n_Auto-maintained. One line per brief. Always loaded by the bot._\n\n"


@dataclass
class IndexEntry:
    file_stem: str
    title: str
    show: str
    date: str
    topics: list[str]
    hook: str          # one-line description used by the bot to decide relevance


def _hook_for(metadata: dict, body: str) -> str:
    """Pick a short, distinctive hook for the index line.

    Prefer the headline → tldr → first non-empty line of body.
    """
    if metadata.get("headline"):
        return str(metadata["headline"]).strip()
    # Try to extract from body sections
    m = re.search(r"## Headline\n(.+?)\n", body)
    if m:
        return m.group(1).strip()
    m = re.search(r"## TL;DR\n(.+?)\n", body)
    if m:
        return m.group(1).strip()
    for line in body.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and not line.startswith("**"):
            return line[:140]
    return ""


def _tokenize(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) > 2]


class ObsidianIndex:
    """Maintains INDEX.md + Obsidian wikilinks across the notes vault.

    On each save:
      1. Add/update entry in INDEX.md (one line: stem, title, show, date, hook).
      2. Append a "Related" wikilink section to the new note based on shared topics.
      3. (Optional) backfill wikilinks the other direction in matching old notes.

    On each query:
      - `pick_relevant(query, k)` runs BM25 over note bodies + frontmatter and returns
        the top-k IndexEntries. The bot can also ask the LLM to filter further from
        INDEX.md text alone for cheap structured retrieval.
    """

    def __init__(self, *, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = base_dir / INDEX_FILENAME

    # ---------------- public API ----------------

    def upsert(self, *, file_stem: str, body: str, metadata: dict) -> None:
        entries = self._load_entries()
        entry = IndexEntry(
            file_stem=file_stem,
            title=str(metadata.get("title", file_stem)),
            show=str(metadata.get("show", "")),
            date=str(metadata.get("date", metadata.get("processed", ""))),
            topics=list(metadata.get("topics", [])),
            hook=_hook_for(metadata, body),
        )
        entries = [e for e in entries if e.file_stem != file_stem]
        entries.append(entry)
        entries.sort(key=lambda e: e.date, reverse=True)
        self._write_entries(entries)
        self._inject_wikilinks(file_stem, entry, entries)

    def all_entries(self) -> list[IndexEntry]:
        return self._load_entries()

    def index_text(self) -> str:
        """The always-loaded one-page index — what the bot sees first."""
        if self.index_path.exists():
            return self.index_path.read_text(encoding="utf-8")
        return INDEX_HEADER

    # Cap per-note body content sent to the LLM. Big enough to include all
    # sections (TL;DR, quotes, numbers, resources) while leaving room for
    # multiple notes plus the index in context.
    BODY_CHAR_LIMIT = 8000
    SMALL_VAULT_THRESHOLD = 5

    def pick_relevant(self, query: str, *, k: int = 5) -> list[tuple[IndexEntry, str]]:
        """Hybrid retrieval. For small vaults (≤5 notes) we send the FULL body
        of every note (capped) so quote/section questions actually have the
        material to answer from. For larger vaults we BM25-rank and send the
        full body of the top-k matches, dropping the transcript section to
        save tokens."""
        entries = self._load_entries()
        if not entries:
            return []

        bodies: list[str] = [self._read_body(e.file_stem) for e in entries]

        if len(entries) <= self.SMALL_VAULT_THRESHOLD:
            return [
                (e, _strip_transcript(b)[: self.BODY_CHAR_LIMIT])
                for e, b in zip(entries, bodies)
            ]

        corpus_tokens: list[list[str]] = []
        for e, body in zip(entries, bodies):
            tokens = _tokenize(
                f"{e.title} {e.show} {' '.join(e.topics)} {e.hook} {body}"
            )
            corpus_tokens.append(tokens or ["_"])
        bm25 = BM25Okapi(corpus_tokens)
        scores = bm25.get_scores(_tokenize(query) or ["_"])
        ranked = sorted(zip(entries, bodies, scores), key=lambda t: t[2], reverse=True)
        out: list[tuple[IndexEntry, str]] = []
        for entry, body, score in ranked[:k]:
            if score <= 0:
                continue
            out.append((entry, _strip_transcript(body)[: self.BODY_CHAR_LIMIT]))
        return out

    def load_full(self, file_stem: str) -> str | None:
        path = self.base_dir / f"{file_stem}.md"
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    # ---------------- internals ----------------

    def _load_entries(self) -> list[IndexEntry]:
        """Walk the vault and build entries from each note's frontmatter.

        The `.md` files are the source of truth; INDEX.md is a derived view
        for Obsidian. This avoids brittle re-parsing of the rendered index.
        """
        out: list[IndexEntry] = []
        for path in sorted(self.base_dir.glob("*.md"), reverse=True):
            if path.name == INDEX_FILENAME:
                continue
            try:
                post = frontmatter.load(str(path))
            except Exception:
                continue
            meta = dict(post.metadata or {})
            body = post.content or ""
            stem = path.stem
            out.append(
                IndexEntry(
                    file_stem=stem,
                    title=str(meta.get("title", stem)),
                    show=str(meta.get("show", "")),
                    date=str(meta.get("date", meta.get("processed", ""))),
                    topics=list(meta.get("topics") or []),
                    hook=_hook_for(meta, body),
                )
            )
        return out

    def _write_entries(self, entries: list[IndexEntry]) -> None:
        lines = [INDEX_HEADER]
        for e in entries:
            topics_str = " ".join(f"#{t}" for t in e.topics) + " " if e.topics else ""
            lines.append(
                f"- [{e.title}]({e.file_stem}.md) · {e.show} · {e.date} · {topics_str}— {e.hook}"
            )
        self.index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _read_body(self, file_stem: str) -> str:
        path = self.base_dir / f"{file_stem}.md"
        if not path.exists():
            return ""
        try:
            post = frontmatter.load(str(path))
            return post.content
        except Exception:
            return path.read_text(encoding="utf-8", errors="ignore")

    def _inject_wikilinks(
        self, file_stem: str, entry: IndexEntry, entries: list[IndexEntry]
    ) -> None:
        """Append a Related section linking to other briefs sharing >=1 topic."""
        if not entry.topics:
            return
        related: list[IndexEntry] = []
        for other in entries:
            if other.file_stem == file_stem:
                continue
            if set(other.topics) & set(entry.topics):
                related.append(other)
            if len(related) >= 5:
                break
        if not related:
            return
        path = self.base_dir / f"{file_stem}.md"
        if not path.exists():
            return
        existing = path.read_text(encoding="utf-8")
        if "## Related" in existing:
            return  # don't duplicate
        block = ["", "## Related", ""]
        block.extend(f"- [[{r.file_stem}|{r.title}]]" for r in related)
        path.write_text(existing.rstrip() + "\n" + "\n".join(block) + "\n", encoding="utf-8")


def _strip_transcript(body: str) -> str:
    """Drop the giant ## Transcript block — the bot never needs verbatim text
    and it eats the whole context window."""
    idx = body.find("\n## Transcript")
    if idx == -1:
        return body
    return body[:idx].rstrip()


def _excerpt(body: str, query: str, *, window: int = 240) -> str:
    """Pull the chunk of body most relevant to query (heading-aware first)."""
    qtokens = set(_tokenize(query))
    if not qtokens:
        return body[:window]
    # Score each section (split on H2)
    sections = re.split(r"\n## ", body)
    best = ""
    best_score = -1
    for s in sections:
        s_tokens = set(_tokenize(s))
        score = len(qtokens & s_tokens)
        if score > best_score:
            best_score = score
            best = s
    if best_score <= 0:
        return body[:window]
    return ("## " + best)[:window * 2].strip()
