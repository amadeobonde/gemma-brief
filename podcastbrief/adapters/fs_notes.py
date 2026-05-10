from __future__ import annotations
import logging
from dataclasses import dataclass
from pathlib import Path
import frontmatter

from podcastbrief.bot.index import INDEX_FILENAME, ObsidianIndex

log = logging.getLogger(__name__)


@dataclass
class StoredNoteRecord:
    file_stem: str
    body: str
    metadata: dict


class FilesystemNoteStore:
    """Saves markdown notes with YAML frontmatter under a base directory.

    Maintains an Obsidian-style INDEX.md and adds wikilinks between briefs that
    share topics. The vault is the source of truth; the bot reads INDEX.md +
    selected note bodies — no external vector DB.
    """

    def __init__(self, *, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._index = ObsidianIndex(base_dir=base_dir)

    def save(self, *, file_stem: str, body: str, metadata: dict) -> str:
        path = self.base_dir / f"{file_stem}.md"
        post = frontmatter.Post(body, **metadata)
        path.write_text(frontmatter.dumps(post), encoding="utf-8")
        log.info("Saved note: %s", path)
        try:
            self._index.upsert(file_stem=file_stem, body=body, metadata=metadata)
        except Exception as e:
            log.warning("Index update failed for %s: %s", file_stem, e)
        return str(path)

    def list_recent(self, limit: int = 30) -> list[StoredNoteRecord]:
        files = [
            f
            for f in sorted(self.base_dir.glob("*.md"), reverse=True)
            if f.name != INDEX_FILENAME
        ][:limit]
        out: list[StoredNoteRecord] = []
        for f in files:
            post = frontmatter.load(str(f))
            out.append(
                StoredNoteRecord(file_stem=f.stem, body=post.content, metadata=dict(post.metadata))
            )
        return out

    def clear(self) -> int:
        n = 0
        for f in self.base_dir.glob("*.md"):
            f.unlink()
            n += 1
        return n

    @property
    def index(self) -> ObsidianIndex:
        return self._index
