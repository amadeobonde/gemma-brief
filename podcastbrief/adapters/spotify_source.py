from __future__ import annotations
import base64
import logging
from datetime import datetime, timedelta, timezone
import httpx
from podcastbrief.core.models import Episode

log = logging.getLogger(__name__)


class SpotifySource:
    """Spotify Web API: list playlist tracks added in the last N hours.

    Uses Client Credentials + Refresh Token flow (the same setup the n8n credential held).
    """

    TOKEN_URL = "https://accounts.spotify.com/api/token"
    API_BASE = "https://api.spotify.com/v1"

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        playlist_id: str,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.playlist_id = playlist_id
        self._token: str | None = None
        self._token_expires: datetime | None = None

    def _access_token(self) -> str:
        if self._token and self._token_expires and datetime.utcnow() < self._token_expires:
            return self._token
        creds = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
        resp = httpx.post(
            self.TOKEN_URL,
            headers={"Authorization": f"Basic {creds}"},
            data={"grant_type": "refresh_token", "refresh_token": self.refresh_token},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        self._token = data["access_token"]
        self._token_expires = datetime.utcnow() + timedelta(seconds=data.get("expires_in", 3600) - 60)
        return self._token

    def list_recent_episodes(self, *, hours: int = 24) -> list[Episode]:
        url = f"{self.API_BASE}/playlists/{self.playlist_id}"
        resp = httpx.get(
            url,
            headers={"Authorization": f"Bearer {self._access_token()}"},
            timeout=60,
        )
        resp.raise_for_status()
        items = resp.json().get("tracks", {}).get("items", [])
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        out: list[Episode] = []
        for it in items:
            track = it.get("track") or {}
            if not track:
                continue
            try:
                added_at = datetime.fromisoformat(it["added_at"].replace("Z", "+00:00"))
            except (KeyError, ValueError):
                continue
            if added_at < cutoff:
                continue
            out.append(
                Episode(
                    episode_id=track.get("id", ""),
                    name=track.get("name", ""),
                    show_name=(track.get("album") or {}).get("name", ""),
                    added_at=added_at,
                    duration_ms=track.get("duration_ms", 0),
                    spotify_url=(track.get("external_urls") or {}).get("spotify", ""),
                )
            )
        log.info("Spotify: %d episodes added in last %dh", len(out), hours)
        return out
