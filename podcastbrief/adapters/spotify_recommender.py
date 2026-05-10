from __future__ import annotations
import logging
from typing import Callable
import httpx
from podcastbrief.core.models import Suggestion

log = logging.getLogger(__name__)


class SpotifyEpisodeRecommender:
    """Searches Spotify for episodes matching a keyword query.

    Reuses the same access-token mechanism as `SpotifySource` via a token-getter callable
    so callers can share a token across instances.
    """

    API = "https://api.spotify.com/v1/search"

    def __init__(self, *, token_getter: Callable[[], str]) -> None:
        self._token_getter = token_getter

    def similar(self, *, query: str, limit: int = 5) -> list[Suggestion]:
        if not query.strip():
            return []
        try:
            r = httpx.get(
                self.API,
                params={"q": query, "type": "episode", "limit": limit},
                headers={"Authorization": f"Bearer {self._token_getter()}"},
                timeout=30,
            )
            r.raise_for_status()
            items = r.json().get("episodes", {}).get("items", [])
            return [
                Suggestion(
                    title=ep.get("name", ""),
                    show=(ep.get("show") or {}).get("name", ""),
                    url=(ep.get("external_urls") or {}).get("spotify", ""),
                )
                for ep in items
                if ep
            ]
        except Exception as e:
            log.warning("Spotify recommend failed: %s", e)
            return []


def keywords_from_brief(why_it_matters: list[str], *, max_words: int = 5) -> str:
    stop = {
        "the", "and", "or", "but", "in", "on", "at", "to", "for", "of", "with", "from",
        "about", "that", "this", "their", "they", "made", "makes", "making",
    }
    seen: list[str] = []
    for line in why_it_matters:
        for w in line.lower().split():
            w = "".join(c for c in w if c.isalpha())
            if len(w) > 4 and w not in stop and w not in seen:
                seen.append(w)
                if len(seen) >= max_words:
                    return " ".join(seen)
    return " ".join(seen) if seen else "podcast"
