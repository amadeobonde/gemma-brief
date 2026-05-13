from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    spotify_client_id: str = ""
    spotify_client_secret: str = ""
    spotify_refresh_token: str = ""
    spotify_playlist_id: str = "2bASZuGKSImz0sw4pE9B8P"

    telegram_bot_token: str = ""
    telegram_chat_ids: str = ""

    ollama_host: str = "http://localhost:11434"
    llm_model: str = "gemma4:e4b"

    whisper_url: str = "http://localhost:9000"
    whisper_timeout_seconds: int = 1800

    gotenberg_url: str = "http://localhost:3000"

    # Telegram voice-reply TTS. With Apple's premium neural voices downloaded
    # (System Settings > Accessibility > Spoken Content > System Voice >
    # Manage Voices), pick a name like "Ava (Premium)" or "Zoe (Premium)".
    tts_voice: str = "Samantha"
    tts_rate: int = 185

    notes_dir: Path = Field(default=Path("./podcast_notes"))
    pdf_out_dir: Path = Field(default=Path("./briefs"))

    log_level: str = "INFO"

    @property
    def chat_id_list(self) -> list[str]:
        return [c.strip() for c in self.telegram_chat_ids.split(",") if c.strip()]


def load_settings() -> Settings:
    return Settings()
