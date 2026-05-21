from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    telegram_bot_token: str = ""
    telegram_chat_ids: str = ""

    ollama_host: str = "http://localhost:11434"
    llm_model: str = "gemma4:e4b"
    # Context window + output budget — auto-set by `gemma-brief setup` based on
    # the chosen model family. Override manually for memory-constrained machines.
    llm_num_ctx: int = 32768
    llm_num_predict: int = 6144

    whisper_url: str = "http://localhost:9000"
    whisper_timeout_seconds: int = 1800

    gotenberg_url: str = "http://localhost:3000"

    # Telegram voice-reply TTS.
    # tts_backend: "auto" detects platform (macos → say, linux → espeak/pyttsx3,
    # windows → pyttsx3). Override with "say", "espeak", "pyttsx3", or "off".
    tts_backend: str = "auto"
    # macOS `say` voice. Run `say -v '?'` for the full list.
    # Premium neural voices: "Ava (Premium)", "Zoe (Premium)", "Evan (Premium)".
    tts_voice: str = "Samantha"
    tts_rate: int = 185

    # Enrichment.
    fred_api_key: str = ""
    rss_news_feeds: str = (
        "https://feeds.reuters.com/reuters/businessNews,"
        "https://www.ft.com/rss/home,"
        "https://feeds.bbci.co.uk/news/business/rss.xml,"
        "https://feeds.bbci.co.uk/news/world/rss.xml"
    )

    @property
    def rss_feed_list(self) -> list[str]:
        return [u.strip() for u in self.rss_news_feeds.split(",") if u.strip()]

    # YouTube playlist URLs (yt-dlp). Comma-separated — add as many as you like.
    # Example: https://www.youtube.com/playlist?list=PLxxxxxxx
    youtube_playlist_urls: str = ""

    @property
    def youtube_playlist_url_list(self) -> list[str]:
        return [u.strip() for u in self.youtube_playlist_urls.split(",") if u.strip()]

    # Podcast RSS feed subscriptions (separate from rss_news_feeds).
    # Comma-separated RSS feed URLs — covers every podcast platform.
    rss_podcast_feeds: str = ""

    @property
    def rss_podcast_feed_list(self) -> list[str]:
        return [u.strip() for u in self.rss_podcast_feeds.split(",") if u.strip()]

    notes_dir: Path = Field(default=Path("./podcast_notes"))
    pdf_out_dir: Path = Field(default=Path("./briefs"))

    # Persistent store of original episode audio (MP3/M4A/OGG). Required by the
    # /debate audio-stitching feature; populated by every pipeline run and
    # backfillable via `podcastbrief redownload-audio`.
    audio_store_path: Path = Field(default=Path("./podcast_notes/audio_store"))

    # /debate audio clip generation tunables.
    clip_padding_seconds: float = 0.75
    clip_silence_between_ms: int = 800
    clip_target_dbfs: float = -18.0

    log_level: str = "INFO"

    @property
    def chat_id_list(self) -> list[str]:
        return [c.strip() for c in self.telegram_chat_ids.split(",") if c.strip()]


def load_settings() -> Settings:
    return Settings()
