"""Register a YouTube playlist or RSS feed in .env without re-running setup.

`podcastbrief add --playlist <url>` and `--rss <url>` append the URL to the
matching env variable, deduping existing entries.
"""
from __future__ import annotations
import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)


_VAR_RE = re.compile(r"^(?P<key>[A-Z_][A-Z0-9_]*)=(?P<val>.*)$")


def _read_env(env_path: Path) -> dict[str, str]:
    if not env_path.exists():
        return {}
    out: dict[str, str] = {}
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _VAR_RE.match(line)
        if not m:
            continue
        out[m.group("key")] = m.group("val").strip().strip('"').strip("'")
    return out


def _write_env(env_path: Path, updates: dict[str, str]) -> None:
    """Merge `updates` into env file, preserving comments and unrelated keys."""
    existing = []
    seen: set[str] = set()
    if env_path.exists():
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.rstrip()
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                existing.append(line)
                continue
            m = _VAR_RE.match(stripped)
            if not m:
                existing.append(line)
                continue
            key = m.group("key")
            if key in updates:
                existing.append(f"{key}={updates[key]}")
                seen.add(key)
            else:
                existing.append(line)
    for key, val in updates.items():
        if key not in seen:
            existing.append(f"{key}={val}")
    env_path.write_text("\n".join(existing).rstrip() + "\n", encoding="utf-8")


def _dedup_csv(existing: str, new: str) -> str:
    items = [x.strip() for x in (existing or "").split(",") if x.strip()]
    if new and new not in items:
        items.append(new)
    return ",".join(items)


def register_source(*, env_path: Path, playlist_url: str = "", rss_url: str = "") -> None:
    if not playlist_url and not rss_url:
        raise SystemExit(
            "Provide a URL: --playlist <youtube-playlist-url> or --rss <feed-url>"
        )

    env = _read_env(env_path)
    updates: dict[str, str] = {}

    if playlist_url:
        merged = _dedup_csv(env.get("YOUTUBE_PLAYLIST_URLS", ""), playlist_url)
        updates["YOUTUBE_PLAYLIST_URLS"] = merged

    if rss_url:
        merged = _dedup_csv(env.get("RSS_PODCAST_FEEDS", ""), rss_url)
        updates["RSS_PODCAST_FEEDS"] = merged

    if not updates:
        print("No changes.")
        return

    _write_env(env_path, updates)
    for key, val in updates.items():
        print(f"  {key}={val}")
    print(f"Wrote {env_path}")
