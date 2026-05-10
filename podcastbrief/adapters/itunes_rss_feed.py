from __future__ import annotations
import logging
import re
from typing import Any
import feedparser
import httpx
from podcastbrief.core.models import Episode, AudioRef

log = logging.getLogger(__name__)


def _normalize_title(t: str) -> str:
    t = t.lower()
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _title_match(spotify_title: str, rss_title: str) -> bool:
    a = _normalize_title(spotify_title)
    b = _normalize_title(rss_title)
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    a_words = {w for w in a.split() if len(w) > 3}
    b_words = {w for w in b.split() if len(w) > 3}
    if not a_words or not b_words:
        return False
    overlap = a_words & b_words
    return len(overlap) >= max(1, int(min(len(a_words), len(b_words)) * 0.5))


class ItunesRssFeed:
    """Resolve episode → audio URL via iTunes search → RSS feed → title match.

    Ported from the n8n workflow's two-step lookup.
    """

    ITUNES_SEARCH = "https://itunes.apple.com/search"

    def find_audio(self, episode: Episode) -> AudioRef:
        # Step 1: iTunes search for the show name → RSS feed URL
        r = httpx.get(
            self.ITUNES_SEARCH,
            params={"term": episode.show_name, "entity": "podcast", "limit": 1},
            timeout=30,
        )
        r.raise_for_status()
        results = r.json().get("results", [])
        if not results:
            raise ValueError(f"Podcast not found in iTunes: {episode.show_name}")
        feed_url = results[0].get("feedUrl")
        if not feed_url:
            raise ValueError(f"iTunes returned no feedUrl for: {episode.show_name}")

        # Step 2: fetch the RSS feed
        rss_resp = httpx.get(feed_url, timeout=60, follow_redirects=True)
        rss_resp.raise_for_status()
        feed = feedparser.parse(rss_resp.content)

        # Step 3: match the episode by title
        match: Any = None
        for entry in feed.entries:
            if _title_match(episode.name, entry.get("title", "")):
                match = entry
                break
        if not match:
            raise ValueError(f"Episode not found in RSS feed for: {episode.name}")

        # Pull the enclosure URL
        mp3_url: str | None = None
        for link in match.get("links", []):
            if link.get("rel") == "enclosure":
                mp3_url = link.get("href")
                break
        if not mp3_url and "enclosures" in match:
            mp3_url = match.enclosures[0].get("href") if match.enclosures else None
        if not mp3_url:
            raise ValueError(f"No audio enclosure found for: {episode.name}")

        return AudioRef(
            url=mp3_url,
            title=match.get("title", episode.name),
            pub_date=match.get("published", None),
            show_name=episode.show_name,
        )


def download_audio(audio_ref: AudioRef, *, timeout: int = 600) -> bytes:
    r = httpx.get(audio_ref.url, timeout=timeout, follow_redirects=True)
    r.raise_for_status()
    return r.content
