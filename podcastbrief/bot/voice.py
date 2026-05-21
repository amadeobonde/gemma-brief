"""Voice-message plumbing for the Telegram bot.

Flow: incoming OGG/Opus voice note -> faster-whisper STT -> RagBot.answer() ->
TTS synthesis -> ffmpeg encode to OGG/Opus -> Telegram sendVoice.

TTS backend selection (auto-detected, or override with TTS_BACKEND in .env):
  macOS   → `say`   (built-in, premium neural voices available)
  Linux   → `espeak-ng`  (apt install espeak-ng)  or pyttsx3 fallback
  Windows → `pyttsx3`   (pip install pyttsx3, uses SAPI voices)
  Any     → "off" to disable TTS (bot replies with text only)
"""
from __future__ import annotations
import logging
import os
import platform
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
import httpx

from podcastbrief.ports.transcriber import Transcriber

log = logging.getLogger(__name__)


# ── ffmpeg path resolution ────────────────────────────────────────────────────
# Prefer ~/.local/bin so the bot can find it under launchd / systemd where PATH
# is minimal. Fall back to common install locations then $PATH.
_FFMPEG_CANDIDATES = [
    os.path.expanduser("~/.local/bin/ffmpeg"),
    "/opt/homebrew/bin/ffmpeg",
    "/usr/local/bin/ffmpeg",
    "/usr/bin/ffmpeg",
]


def _ffmpeg_path() -> str | None:
    for p in _FFMPEG_CANDIDATES:
        if Path(p).is_file() and os.access(p, os.X_OK):
            return p
    return shutil.which("ffmpeg")


# ── TTS backend detection ─────────────────────────────────────────────────────

def _detect_tts_backend(override: str = "auto") -> str:
    """Return the TTS backend to use: 'say', 'espeak', 'pyttsx3', or 'off'."""
    if override and override != "auto":
        return override
    system = platform.system()
    if system == "Darwin":
        return "say"
    if system == "Linux":
        if shutil.which("espeak-ng"):
            return "espeak"
        if shutil.which("espeak"):
            return "espeak"
        try:
            import pyttsx3  # noqa: F401
            return "pyttsx3"
        except ImportError:
            pass
        log.warning("No TTS engine found on Linux. Install: sudo apt install espeak-ng")
        return "off"
    if system == "Windows":
        try:
            import pyttsx3  # noqa: F401
            return "pyttsx3"
        except ImportError:
            log.warning("pyttsx3 not installed. Run: pip install pyttsx3")
            return "off"
    return "off"


@dataclass
class VoiceConfig:
    # TTS backend override — "auto" picks the right one for the OS.
    tts_backend: str = "auto"
    # macOS `say` voice.  Run `say -v '?'` to list all voices.
    # Premium neural: "Ava (Premium)", "Zoe (Premium)", "Evan (Premium)".
    voice: str = "Samantha"
    # Linux espeak voice name (e.g. "en", "en-us", "en-gb").
    espeak_voice: str = "en"
    # Words per minute (macOS say / espeak rate).
    rate: int = 185
    # Hard cap on TTS input chars — prevents the bot rambling for a full minute.
    max_chars: int = 600
    opus_bitrate_kbps: int = 32   # Telegram voice notes: 24–48 kbps sounds fine


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
        self._tts_backend = _detect_tts_backend(self.config.tts_backend)
        if not self._ffmpeg:
            log.warning("ffmpeg not found; voice responses will be M4A (macOS) or disabled.")
        log.info("VoiceProcessor: tts_backend=%s  ffmpeg=%s", self._tts_backend, self._ffmpeg)

    # ── inbound ──────────────────────────────────────────────────────────────

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
        """OGG/Opus → text via the shared Transcriber."""
        t = self.transcriber.transcribe(audio, filename="voice.ogg")
        return (t.text or "").strip()

    # ── outbound ─────────────────────────────────────────────────────────────

    def synthesize(self, text: str) -> tuple[bytes, str, str] | None:
        """Render `text` to audio. Returns (bytes, mime_type, filename) or None.

        Returns None when TTS is unavailable (backend = 'off'), so callers
        can fall back to sending a text reply instead.

        Output format:
          - OGG/Opus if ffmpeg is available → Telegram sendVoice (bubble UX)
          - M4A via afconvert (macOS only, no ffmpeg) → Telegram sendAudio
          - None if no TTS engine found
        """
        if self._tts_backend == "off":
            return None

        clean = self._prep_text_for_tts(text)
        with tempfile.TemporaryDirectory() as td:
            wav = Path(td) / "out.wav"
            ok = self._tts_to_wav(clean, wav)
            if not ok:
                return None

            if self._ffmpeg:
                ogg = Path(td) / "out.ogg"
                subprocess.run(
                    [
                        self._ffmpeg,
                        "-y", "-hide_banner", "-loglevel", "error",
                        "-i", str(wav),
                        "-c:a", "libopus",
                        "-b:a", f"{self.config.opus_bitrate_kbps}k",
                        "-application", "voip",
                        str(ogg),
                    ],
                    check=True,
                )
                return ogg.read_bytes(), "audio/ogg", "response.ogg"

            # macOS fallback: AIFF → M4A via afconvert (no ffmpeg needed).
            if platform.system() == "Darwin":
                m4a = Path(td) / "out.m4a"
                subprocess.run(
                    ["/usr/bin/afconvert", "-f", "m4af", "-d", "aac",
                     str(wav), str(m4a)],
                    check=True,
                )
                return m4a.read_bytes(), "audio/mp4", "response.m4a"

            # No conversion path — return raw WAV.
            return wav.read_bytes(), "audio/wav", "response.wav"

    @property
    def has_tts(self) -> bool:
        return self._tts_backend != "off"

    @property
    def has_opus(self) -> bool:
        return self._ffmpeg is not None

    # ── TTS engine dispatch ───────────────────────────────────────────────────

    def _tts_to_wav(self, text: str, out_wav: Path) -> bool:
        """Render `text` to a WAV file using the active backend. Returns True on success."""
        try:
            if self._tts_backend == "say":
                return self._tts_say(text, out_wav)
            if self._tts_backend == "espeak":
                return self._tts_espeak(text, out_wav)
            if self._tts_backend == "pyttsx3":
                return self._tts_pyttsx3(text, out_wav)
        except Exception as exc:
            log.warning("TTS synthesis failed (%s): %s", self._tts_backend, exc)
        return False

    def _tts_say(self, text: str, out_wav: Path) -> bool:
        """macOS `say` → AIFF → WAV via ffmpeg (or leave as AIFF for afconvert)."""
        aiff = out_wav.with_suffix(".aiff")
        subprocess.run(
            [
                "/usr/bin/say",
                "-v", self.config.voice,
                "-r", str(self.config.rate),
                "-o", str(aiff),
                text,
            ],
            check=True,
        )
        if self._ffmpeg:
            subprocess.run(
                [self._ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                 "-i", str(aiff), str(out_wav)],
                check=True,
            )
        else:
            # Leave as AIFF — the caller's afconvert path will handle it.
            out_wav.write_bytes(aiff.read_bytes())
        return True

    def _tts_espeak(self, text: str, out_wav: Path) -> bool:
        """Linux espeak-ng → WAV."""
        binary = shutil.which("espeak-ng") or shutil.which("espeak") or "espeak-ng"
        # espeak rate: 80–450 wpm (default 175). Map our wpm directly.
        subprocess.run(
            [
                binary,
                "-v", self.config.espeak_voice,
                "-s", str(self.config.rate),
                "-w", str(out_wav),
                text,
            ],
            check=True,
        )
        return True

    def _tts_pyttsx3(self, text: str, out_wav: Path) -> bool:
        """Cross-platform pyttsx3 (Windows SAPI / Linux espeak backend)."""
        import pyttsx3
        engine = pyttsx3.init()
        engine.setProperty("rate", self.config.rate)
        engine.save_to_file(text, str(out_wav))
        engine.runAndWait()
        return out_wav.exists() and out_wav.stat().st_size > 0

    # ── text preprocessing ────────────────────────────────────────────────────

    def _prep_text_for_tts(self, text: str) -> str:
        """Trim and strip markup that doesn't read well aloud."""
        s = text.strip()
        s = re.sub(r"\[\[([^\]|]+)\|([^\]]+)\]\]", r"\2", s)
        s = re.sub(r"\[\[([^\]]+)\]\]", r"\1", s)
        s = s.replace("**", "").replace("__", "")
        s = re.sub(r"`([^`]+)`", r"\1", s)
        if len(s) > self.config.max_chars:
            s = s[: self.config.max_chars].rsplit(" ", 1)[0] + "…"
        return s
