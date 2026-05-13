from __future__ import annotations
from typing import Any, Protocol, Sequence, TypeVar
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLM(Protocol):
    def complete(
        self,
        *,
        system: str,
        user: str,
        images: Sequence[bytes] | None = None,
        temperature: float = 0.4,
        num_predict: int | None = None,
    ) -> str: ...

    def json_complete(
        self,
        *,
        system: str,
        user: str,
        schema: type[T],
        images: Sequence[bytes] | None = None,
        temperature: float = 0.2,
        max_retries: int = 1,
    ) -> T: ...
