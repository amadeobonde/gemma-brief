"""YouTubeRecommender — find related YouTube videos via yt-dlp search."""
from __future__ import annotations

import json
import logging
import subprocess

from podcastbrief.adapters._yt_dlp import yt_dlp_path
from podcastbrief.core.models import Suggestion

log = logging.getLogger(__name__)


class YouTubeRecommender:
    """Search YouTube for videos related to a query using yt-dlp.

    Uses yt-dlp's ytsearch: pseudo-URL which requires no API key and stays
    within YouTube's public search — no credentials needed.
    """

    def __init__(self, max_results: int = 5) -> None:
        self.max_results = max_results

    def similar(self, *, query: str, limit: int = 5) -> list[Suggestion]:
        if not query.strip():
            return []
        yt = yt_dlp_path()
        if not yt:
            log.warning("yt-dlp not found — YouTube recommendations skipped")
            return []

        n = min(limit, self.max_results)
        cmd = [
            yt,
            f"ytsearch{n}:{query}",
            "--dump-single-json",
            "--flat-playlist",
            "--no-warnings",
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired:
            log.warning("YouTube search timed out for query: %r", query)
            return []

        if proc.returncode != 0:
            log.warning("yt-dlp search error: %s", (proc.stderr or "")[:300])
            return []

        try:
            data = json.loads(proc.stdout)
        except (json.JSONDecodeError, ValueError):
            return []

        results: list[Suggestion] = []
        for entry in data.get("entries") or []:
            if not entry:
                continue
            video_id = entry.get("id") or ""
            title = (entry.get("title") or "").strip()
            channel = (entry.get("uploader") or entry.get("channel") or "YouTube").strip()
            if not video_id or not title:
                continue
            results.append(
                Suggestion(
                    title=title,
                    show=channel,
                    url=f"https://www.youtube.com/watch?v={video_id}",
                )
            )
        return results
