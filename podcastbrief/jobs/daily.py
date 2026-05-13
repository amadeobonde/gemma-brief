from __future__ import annotations
import logging
from podcastbrief.core.config import Settings
from podcastbrief.core.pipeline import Pipeline
from podcastbrief.adapters.spotify_source import SpotifySource
from podcastbrief.adapters.itunes_rss_feed import ItunesRssFeed, download_audio
from podcastbrief.adapters.whisper_http import WhisperHttpTranscriber
from podcastbrief.adapters.ollama_gemma import OllamaGemma
from podcastbrief.adapters.artwork_rss import ItunesArtworkProvider
from podcastbrief.adapters.gotenberg_renderer import GotenbergRenderer
from podcastbrief.adapters.telegram_notifier import TelegramNotifier
from podcastbrief.adapters.fs_notes import FilesystemNoteStore
from podcastbrief.adapters.spotify_recommender import SpotifyEpisodeRecommender
from podcastbrief.adapters.yahoo_enricher import YahooFinanceEnricher
from podcastbrief.adapters.fred_enricher import FREDEnricher
from podcastbrief.adapters.wikipedia_enricher import WikipediaEnricher
from podcastbrief.adapters.rss_news_enricher import RSSNewsEnricher

log = logging.getLogger(__name__)


def build_pipeline(s: Settings) -> Pipeline:
    spotify = SpotifySource(
        client_id=s.spotify_client_id,
        client_secret=s.spotify_client_secret,
        refresh_token=s.spotify_refresh_token,
        playlist_id=s.spotify_playlist_id,
    )
    enrichers = [
        YahooFinanceEnricher(),
        FREDEnricher(api_key=s.fred_api_key),
        WikipediaEnricher(),
        RSSNewsEnricher(feeds=s.rss_feed_list),
    ]
    return Pipeline(
        source=spotify,
        feed=ItunesRssFeed(),
        transcriber=WhisperHttpTranscriber(
            base_url=s.whisper_url, timeout_seconds=s.whisper_timeout_seconds
        ),
        llm=OllamaGemma(host=s.ollama_host, model=s.llm_model),
        images=ItunesArtworkProvider(),
        renderer=GotenbergRenderer(base_url=s.gotenberg_url),
        notifier=TelegramNotifier(bot_token=s.telegram_bot_token),
        notes=FilesystemNoteStore(base_dir=s.notes_dir),
        recommender=SpotifyEpisodeRecommender(token_getter=spotify._access_token),
        chat_ids=s.chat_id_list,
        audio_downloader=download_audio,
        pdf_out_dir=s.pdf_out_dir,
        enrichers=enrichers,
        notes_dir=s.notes_dir,
    )


def run_daily(*, dry_run: bool = False, hours: int = 24) -> int:
    from podcastbrief.core.config import load_settings

    s = load_settings()
    logging.basicConfig(level=getattr(logging, s.log_level.upper(), logging.INFO))
    pipe = build_pipeline(s)
    return pipe.run_daily(hours=hours, dry_run=dry_run)
