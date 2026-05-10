from __future__ import annotations
import io
import logging
import httpx
from PIL import Image
from podcastbrief.core.models import Episode

log = logging.getLogger(__name__)


class ItunesArtworkProvider:
    """Pulls episode/show artwork from iTunes (high-res).

    Strategy: hit the iTunes search endpoint for the show, take artworkUrl600,
    upscale URL pattern to 1200x1200 if available. Returns PNG bytes.
    """

    ITUNES_SEARCH = "https://itunes.apple.com/search"

    def artwork(self, episode: Episode) -> bytes | None:
        try:
            r = httpx.get(
                self.ITUNES_SEARCH,
                params={"term": episode.show_name, "entity": "podcast", "limit": 1},
                timeout=20,
            )
            r.raise_for_status()
            results = r.json().get("results", [])
            if not results:
                return None
            art_url = (
                results[0].get("artworkUrl600")
                or results[0].get("artworkUrl100")
            )
            if not art_url:
                return None
            # iTunes URLs are templated with size in the path; bump to 1200
            art_url = art_url.replace("100x100bb", "1200x1200bb").replace(
                "600x600bb", "1200x1200bb"
            )
            img_resp = httpx.get(art_url, timeout=30, follow_redirects=True)
            img_resp.raise_for_status()
            return _to_png(img_resp.content)
        except Exception as e:
            log.warning("Artwork fetch failed for %s: %s", episode.show_name, e)
            return None


def _to_png(raw: bytes) -> bytes:
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
