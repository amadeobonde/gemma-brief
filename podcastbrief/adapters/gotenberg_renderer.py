from __future__ import annotations
import base64
import io
from importlib import resources
import httpx
from jinja2 import Environment, FileSystemLoader, select_autoescape
from PIL import Image

from podcastbrief.briefing.schemas import RenderInput


_KIND_ICONS = {
    "book": "📕",
    "paper": "📄",
    "tool": "🛠",
    "person": "👤",
    "company": "🏢",
    "article": "📰",
    "other": "•",
}


def _kind_icon(kind: str) -> str:
    return _KIND_ICONS.get(kind, "•")


def _dominant_color_hex(png_bytes: bytes, fallback: str = "#6c63ff") -> str:
    try:
        img = Image.open(io.BytesIO(png_bytes)).convert("RGB").resize((48, 48))
        pixels = list(img.getdata())
        r = sum(p[0] for p in pixels) // len(pixels)
        g = sum(p[1] for p in pixels) // len(pixels)
        b = sum(p[2] for p in pixels) // len(pixels)
        avg = (r + g + b) / 3
        if abs(r - avg) + abs(g - avg) + abs(b - avg) < 30:
            return fallback
        return f"#{r:02x}{g:02x}{b:02x}"
    except Exception:
        return fallback


def _to_data_uri(png_bytes: bytes, mime: str = "image/png") -> str:
    b64 = base64.b64encode(png_bytes).decode("ascii")
    return f"data:{mime};base64,{b64}"


class GotenbergRenderer:
    """HTML -> PDF via a Gotenberg Chromium endpoint.

    Uses the same Jinja2 morning-brief template as the WeasyPrint renderer, but
    sends the rendered HTML to Gotenberg's /forms/chromium/convert/html and
    returns the PDF bytes. Artwork is embedded as a data URI so no extra files
    need to be uploaded.
    """

    def __init__(self, *, base_url: str = "http://localhost:3000", timeout: int = 120) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        templates_path = resources.files("podcastbrief.briefing").joinpath("templates")
        self._env = Environment(
            loader=FileSystemLoader(str(templates_path)),
            autoescape=select_autoescape(["html", "xml"]),
        )
        self._env.globals["kind_icon"] = _kind_icon
        self._template = self._env.get_template("morning_brief.html")
        with templates_path.joinpath("morning_brief.css").open("r", encoding="utf-8") as f:
            self._css = f.read()

    def render(self, brief: RenderInput) -> bytes:
        accent = (
            _dominant_color_hex(brief.artwork_png) if brief.artwork_png else "#6c63ff"
        )
        hero_data_uri = _to_data_uri(brief.artwork_png) if brief.artwork_png else None
        enrichment = brief.enrichment
        wiki = list(getattr(enrichment, "wiki", None) or [])
        news = list(getattr(enrichment, "news", None) or [])
        html_str = self._template.render(
            render=brief,
            b=brief.brief,
            css=self._css,
            accent=accent,
            hero_data_uri=hero_data_uri,
            language=getattr(brief.brief, "language", "en"),
            wiki=wiki,
            news=news,
        )
        files = {"files": ("index.html", html_str.encode("utf-8"), "text/html")}
        url = f"{self.base_url}/forms/chromium/convert/html"
        resp = httpx.post(url, files=files, timeout=self.timeout)
        resp.raise_for_status()
        return resp.content
