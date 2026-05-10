from __future__ import annotations
import base64
import json
import logging
from typing import Sequence, TypeVar
from pydantic import BaseModel, ValidationError
import ollama

T = TypeVar("T", bound=BaseModel)

log = logging.getLogger(__name__)


# Default context window. Ollama's library default is 2048 (way too small for full
# transcripts). Gemma 4 supports 128K, but bigger ctx = more KV cache memory; pick a
# value that comfortably holds an hour-long transcript + schema + answer headroom.
DEFAULT_NUM_CTX = 32768
DEFAULT_NUM_PREDICT = 6144
KEEP_ALIVE = "30m"  # keep model resident across the 6+ calls per episode


class OllamaGemma:
    """Multimodal LLM adapter for Gemma 4 via Ollama.

    `gemma4:e4b` accepts interleaved text + image input and supports JSON-mode output.
    """

    def __init__(
        self,
        *,
        host: str = "http://localhost:11434",
        model: str = "gemma4:e4b",
        num_ctx: int = DEFAULT_NUM_CTX,
        num_predict: int = DEFAULT_NUM_PREDICT,
    ) -> None:
        self._client = ollama.Client(host=host)
        self.model = model
        self.num_ctx = num_ctx
        self.num_predict = num_predict

    def _options(self, *, temperature: float, num_predict: int | None = None) -> dict:
        return {
            "temperature": temperature,
            "num_ctx": self.num_ctx,
            "num_predict": num_predict if num_predict is not None else self.num_predict,
        }

    def complete(
        self,
        *,
        system: str,
        user: str,
        images: Sequence[bytes] | None = None,
        temperature: float = 0.4,
        num_predict: int | None = None,
    ) -> str:
        msgs = [{"role": "system", "content": system}, self._user_msg(user, images)]
        resp = self._client.chat(
            model=self.model,
            messages=msgs,
            options=self._options(temperature=temperature, num_predict=num_predict),
            keep_alive=KEEP_ALIVE,
        )
        return resp["message"]["content"]

    def json_complete(
        self,
        *,
        system: str,
        user: str,
        schema: type[T],
        images: Sequence[bytes] | None = None,
        temperature: float = 0.2,
        max_retries: int = 1,
        example: str | None = None,
    ) -> T:
        # When `example` is provided, send a concrete shape example (much more
        # reliable than dumping the full JSON schema — Gemma in JSON mode tends to
        # echo the schema itself instead of producing data when given a schema).
        if example:
            shape_directive = (
                "Return ONLY a JSON object with EXACTLY this shape (replace the "
                "example values with real data; keep all field names verbatim; do "
                "not include the field 'properties', 'title', 'type', or any other "
                "JSON-schema metadata):\n" + example
            )
        else:
            schema_hint = json.dumps(schema.model_json_schema(), separators=(",", ":"))
            shape_directive = (
                "Return ONLY valid JSON conforming exactly to this schema:\n" + schema_hint
            )
        sys_with_schema = f"{system}\n\n{shape_directive}"
        last_err: Exception | None = None
        prompt = user
        for attempt in range(max_retries + 1):
            msgs = [
                {"role": "system", "content": sys_with_schema},
                self._user_msg(prompt, images),
            ]
            resp = self._client.chat(
                model=self.model,
                messages=msgs,
                format="json",
                options=self._options(temperature=temperature),
                keep_alive=KEEP_ALIVE,
            )
            raw = resp["message"]["content"]
            try:
                data = json.loads(raw)
                # Gemma in JSON mode sometimes wraps output under the schema name,
                # e.g. {"QuoteSelection": {...}} instead of {...}. Unwrap if so.
                if isinstance(data, dict) and len(data) == 1:
                    only_key = next(iter(data))
                    title = schema.__name__.lstrip("_")
                    if (
                        only_key.lower() == schema.__name__.lower()
                        or only_key.lower() == title.lower()
                    ) and isinstance(data[only_key], dict):
                        data = data[only_key]
                return schema.model_validate(data)
            except (json.JSONDecodeError, ValidationError) as e:
                last_err = e
                preview = (raw or "").strip().replace("\n", " ")[:300]
                log.warning(
                    "JSON parse/validate failed (attempt %d): %s | raw[:300]=%r",
                    attempt + 1,
                    e,
                    preview,
                )
                prompt = (
                    f"{user}\n\nYour previous response failed validation with: {e}\n"
                    f"Return ONLY valid JSON matching the schema. No prose."
                )
        raise RuntimeError(f"json_complete failed after retries: {last_err}")

    @staticmethod
    def _user_msg(text: str, images: Sequence[bytes] | None) -> dict:
        msg: dict = {"role": "user", "content": text}
        if images:
            msg["images"] = [base64.b64encode(img).decode("ascii") for img in images]
        return msg
