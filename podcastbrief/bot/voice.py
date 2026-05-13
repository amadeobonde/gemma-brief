"""Voice-message plumbing for the Telegram bot.

Flow: incoming OGG/Opus voice note -> faster-whisper STT -> RagBot.answer() ->
macOS `say` TTS to AIFF -> ffmpeg encode to OGG/Opus -> Telegram sendVoice.

We use the existing Transcriber port for STT (so swapping Whisper for another
provider also swaps voice STT) and a small TTS helper here. TTS could likewise
be promoted to a Protocol later; keeping it inline for now since macOS `say`
covers the test rig.
"""
from __future__ import annotations
import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
import httpx

from podcastbrief.ports.transcriber import Transcriber

log = logging.getLogger(__name__)


# macOS ships `say` and `afconvert`. ffmpeg is installed standalone (we don't
# require Homebrew); prefer ~/.local/bin so the bot can find it even when run
# under launchd where PATH is minimal.
_FFMPEG_CANDIDATES = [
    os.path.expanduser("~/.local/bin/ffmpeg"),
    "/opt/homebrew/bin/ffmpeg",
    "/usr/local/bin/ffmpeg",
]


def _ffmpeg_path() -> str | None:
    for p in _FFMPEG_CANDIDATES:
        if Path(p).is_file() and os.access(p, os.X_OK):
            return p
    return shutil.which("ffmpeg")


@dataclass
class VoiceConfig:
    voice: str = "Samantha"          # macOS `say` voice (run `say -v '?'` to list)
    rate: int = 185                  # words per minute
    max_chars: int = 1200            # truncate long answers before TTS
    opus_bitrate_kbps: int = 32      # Telegram voice notes do well around 24-48k


class VoiceProcessor:
    """STT + TTS bridge between Telegram voice messages and the RAG bot."""

    def __init__(
        self,
        *,
        transcriber: Transcriber,
        bot_token: str,
        config: VoiceConfig | None = None,
    ) -> None:
        self.transcriber = transcriber
        self.bot_token = bot_token
        self.config = config or VoiceConfig()
        self._ffmpeg = _ffmpeg_path()
        if not self._ffmpeg:
            log.warning("ffmpeg not found; voice responses will fall back to M4A via afconvert.")

    # ---------------- inbound ----------------

    def download_voice(self, file_id: str) -> bytes:
        """Download a Telegram voice file (OGG/Opus) by file_id."""
        info_url = f"https://api.telegram.org/bot{self.bot_token}/getFile"
        r = httpx.get(info_url, params={"file_id": file_id}, timeout=30)
        r.raise_for_status()
        file_path = r.json()["result"]["file_path"]
        dl_url = f"https://api.telegram.org/file/bot{self.bot_token}/{file_path}"
        audio = httpx.get(dl_url, timeout=120)
        audio.raise_for_status()
        return audio.content

    def transcribe(self, audio: bytes) -> str:
        """OGG/Opus -> text via the shared Transcriber."""
        t = self.transcriber.transcribe(audio, filename="voice.ogg")
        return (t.text or "").strip()

    # ---------------- outbound ----------------

    def synthesize(self, text: str) -> tuple[bytes, str, str]:
        """Render `text` to audio. Returns (bytes, mime_type, suggested_filename).

        Prefers OGG/Opus (so we can use Telegram's sendVoice — the voice bubble
        UX). Falls back to M4A via afconvert if ffmpeg is missing.
        """
        clean = self._prep_text_for_tts(text)
        with tempfile.TemporaryDirectory() as td:
            aiff = Path(td) / "out.aiff"
            subprocess.run(
                [
                    "/usr/bin/say",
                    "-v", self.config.voice,
                    "-r", str(self.config.rate),
                    "-o", str(aiff),
                    clean,
                ],
                check=True,
            )

            if self._ffmpeg:
                ogg = Path(td) / "out.ogg"
                subprocess.run(
                    [
                        self._ffmpeg,
                        "-y", "-hide_banner", "-loglevel", "error",
                        "-i", str(aiff),
                        "-c:a", "libopus",
                        "-b:a", f"{self.config.opus_bitrate_kbps}k",
                        "-application", "voip",
                        str(ogg),
                    ],
                    check=True,
                )
                return ogg.read_bytes(), "audio/ogg", "response.ogg"

            # Fallback: m4a via afconvert (no ffmpeg). Goes through sendAudio.
            m4a = Path(td) / "out.m4a"
            subprocess.run(
                [
                    "/usr/bin/afconvert",
                    "-f", "m4af", "-d", "aac",
                    str(aiff), str(m4a),
                ],
                check=True,
            )
            return m4a.read_bytes(), "audio/mp4", "response.m4a"

    @property
    def has_opus(self) -> bool:
        return self._ffmpeg is not None

    # ---------------- internals ----------------

    def _prep_text_for_tts(self, text: str) -> str:
        """Trim and strip markup that doesn't read well aloud."""
        s = text.strip()
        # Drop wikilinks: [[stem|title]] -> title; [[stem]] -> stem
        import re
        s = re.sub(r"\[\[([^\]|]+)\|([^\]]+)\]\]", r"\2", s)
        s = re.sub(r"\[\[([^\]]+)\]\]", r"\1", s)
        # Drop bare markdown formatting that screen-reads badly
        s = s.replace("**", "").replace("__", "")
        s = re.sub(r"`([^`]+)`", r"\1", s)
        if len(s) > self.config.max_chars:
            s = s[: self.config.max_chars].rsplit(" ", 1)[0] + "…"
        return s
