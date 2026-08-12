"""
Main entry point - runs both factor pipelines indefinitely.

    Module A:  RSS -> news_crawler -> NEWS -> scorer -> SENTIMENT_SCORE
                                                     -> aggregator
    Module B:  Binance -> kline_fetcher -> KLINE -> indicator_engine
                                                 -> TECHNICAL_SCORE

    Both factor streams -> storage -> SQLite

Runs until interrupted with Ctrl+C. Poll intervals are controlled by
config.yaml (news_crawler.poll_interval, technical.poll_interval).
"""

import asyncio
import signal

from loguru import logger

from core.config import load_config
from core.database import init_db, get_db_path
from core.event_bus import EventBus
from plugins.sentiment.news_crawler import NewsCrawler
from plugins.sentiment.scorer import SentimentScorer
from plugins.sentiment.aggregator import SentimentAggregator
from plugins.technical.kline_fetcher import KlineFetcher
from plugins.technical.indicator_engine import IndicatorEngine
from plugins.storage.storage_plugin import StoragePlugin


async def main() -> None:
    await init_db()
    config = load_config()
    bus = EventBus()

    # --- Module A: sentiment ---
    crawler = NewsCrawler(bus, config=config["news_crawler"])
    scorer = SentimentScorer(bus, config=config)
    aggregator = SentimentAggregator(bus, config=config)

    # --- Module B: technical ---
    kline_fetcher = KlineFetcher(bus, config=config["technical"])
    indicator_engine = IndicatorEngine(bus, config=config)

    # --- Persistence ---
    storage = StoragePlugin(bus, config=config)

    # Start order matters: subscribers must be running before publishers,
    # otherwise the first batch of events is dispatched with no listeners
    # and silently dropped.
    plugins = [
        storage,            # subscribes to everything
        aggregator,         # subscribes to SENTIMENT_SCORE
        scorer,             # subscribes to NEWS, publishes SENTIMENT_SCORE
        indicator_engine,   # subscribes to KLINE, publishes TECHNICAL_SCORE
        crawler,            # publishes NEWS
        kline_fetcher,      # publishes KLINE
    ]

    await bus.start()
    for p in plugins:
        await p.start()

    logger.info(f"Pipeline running. DB at {get_db_path()}")
    logger.info(f"News poll:  {config['news_crawler']['poll_interval']}s")
    logger.info(f"Kline poll: {config['technical']['poll_interval']}s")
    logger.info("Press Ctrl+C to stop.")

    stop_event = asyncio.Event()

    def _request_stop() -> None:
        logger.info("Shutdown signal received, stopping gracefully...")
        stop_event.set()

    # add_signal_handler is not supported on the default Windows event
    # loop; fall back to catching KeyboardInterrupt instead.
    try:
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGINT, _request_stop)
    except NotImplementedError:
        pass

    try:
        await stop_event.wait()
    except KeyboardInterrupt:
        logger.info("Shutdown signal received, stopping gracefully...")

    # Shut down in reverse order: stop publishers first, so subscribers
    # can drain what is already in flight before they close.
    for p in reversed(plugins):
        await p.stop()
    await bus.stop()
    logger.info("Shutdown complete.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass