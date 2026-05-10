from __future__ import annotations
from typing import Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from podcastbrief.briefing.schemas import RenderInput


class BriefRenderer(Protocol):
    def render(self, brief: "RenderInput") -> bytes: ...
