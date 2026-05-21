from __future__ import annotations
import logging
from podcastbrief.core.config import Settings
from podcastbrief.core.pipeline import Pipeline
from podcastbrief.adapters.youtube_source import YouTubePlaylistSource
from podcastbrief.adapters.youtube_feed import YouTubeFeedResolver, youtube_download_audio
from podcastbrief.adapters.itunes_rss_feed import download_audio
from podcastbrief.adapters.whisper_http import WhisperHttpTranscriber
from podcastbrief.adapters.ollama_gemma import OllamaGemma
from podcastbrief.adapters.artwork_rss import ItunesArtworkProvider
from podcastbrief.adapters.gotenberg_renderer import GotenbergRenderer
from podcastbrief.adapters.telegram_notifier import TelegramNotifier
from podcastbrief.adapters.fs_notes import FilesystemNoteStore
from podcastbrief.adapters.youtube_recommender import YouTubeRecommender
from podcastbrief.adapters.yahoo_enricher import YahooFinanceEnricher
from podcastbrief.adapters.fred_enricher import FREDEnricher
from podcastbrief.adapters.wikipedia_enricher import WikipediaEnricher
from podcastbrief.adapters.rss_news_enricher import RSSNewsEnricher

log = logging.getLogger(__name__)


def _build_extra_bundles(s: Settings) -> list[tuple]:
    """Build (source, feed_resolver, audio_downloader) tuples for additional sources."""
    bundles = []

    # Additional YouTube playlist URLs beyond the first one (already used as primary).
    for url in s.youtube_playlist_url_list[1:]:
        log.info("Extra YouTube playlist source: %s", url)
        bundles.append((
            YouTubePlaylistSource(url),
            YouTubeFeedResolver(),
            youtube_download_audio,
        ))

    # RSS podcast feed subscriptions — covers any platform with an RSS feed.
    if s.rss_podcast_feed_list:
        from podcastbrief.adapters.rss_source import RssFeedSource
        from podcastbrief.adapters.youtube_feed import DirectAudioFeedResolver
        log.info("RSS podcast source enabled: %d feed(s)", len(s.rss_podcast_feed_list))
        bundles.append((
            RssFeedSource(s.rss_podcast_feed_list),
            DirectAudioFeedResolver(),
            download_audio,
        ))

    return bundles


def build_pipeline(s: Settings) -> Pipeline:
    urls = s.youtube_playlist_url_list
    if not urls:
        raise RuntimeError(
            "YOUTUBE_PLAYLIST_URLS is not set in .env. "
            "Add at least one YouTube playlist URL to get started."
        )

    primary_source = YouTubePlaylistSource(urls[0])
    primary_feed = YouTubeFeedResolver()

    enrichers = [
        YahooFinanceEnricher(),
        FREDEnricher(api_key=s.fred_api_key),
        WikipediaEnricher(),
        RSSNewsEnricher(feeds=s.rss_feed_list),
    ]
    return Pipeline(
        source=primary_source,
        feed=primary_feed,
        transcriber=WhisperHttpTranscriber(
            base_url=s.whisper_url, timeout_seconds=s.whisper_timeout_seconds
        ),
        llm=OllamaGemma(
            host=s.ollama_host,
            model=s.llm_model,
            num_ctx=s.llm_num_ctx,
            num_predict=s.llm_num_predict,
        ),
        images=ItunesArtworkProvider(),
        renderer=GotenbergRenderer(base_url=s.gotenberg_url),
        notifier=TelegramNotifier(bot_token=s.telegram_bot_token),
        notes=FilesystemNoteStore(base_dir=s.notes_dir),
        recommender=YouTubeRecommender(),
        chat_ids=s.chat_id_list,
        audio_downloader=youtube_download_audio,
        pdf_out_dir=s.pdf_out_dir,
        enrichers=enrichers,
        notes_dir=s.notes_dir,
        audio_store_dir=s.audio_store_path,
        extra_source_bundles=_build_extra_bundles(s),
    )


def run_daily(*, dry_run: bool = False, hours: int = 24) -> int:
    from podcastbrief.core.config import load_settings

    s = load_settings()
    logging.basicConfig(level=getattr(logging, s.log_level.upper(), logging.INFO))
    pipe = build_pipeline(s)
    return pipe.run_daily(hours=hours, dry_run=dry_run)
