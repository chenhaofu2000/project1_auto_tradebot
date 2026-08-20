"""
End-to-end test: pipeline + storage.

    RSS -> news_crawler -> NEWS -> scorer -> SENTIMENT_SCORE -> storage (SQLite)
                                          -> aggregator

Run with:
    uv run python -m tests.test_storage

After running, inspect data/tradebot.db with any SQLite viewer, or
run the verification queries printed at the end.
"""

import asyncio

import aiosqlite
from loguru import logger

from core.config import load_config
from core.database import init_db, get_db_path
from core.event_bus import EventBus
from plugins.sentiment.news_crawler import NewsCrawler
from plugins.sentiment.scorer import SentimentScorer
from plugins.sentiment.aggregator import SentimentAggregator
from plugins.storage.storage_plugin import StoragePlugin


async def run_test() -> None:
    await init_db()
    config = load_config()
    bus = EventBus()

    crawler = NewsCrawler(bus, config={
        "poll_interval": 30.0,
        "request_timeout": config["news_crawler"]["request_timeout"],
    })
    scorer = SentimentScorer(bus, config=config)
    aggregator = SentimentAggregator(bus, config=config)
    storage = StoragePlugin(bus, config={"storage": {"flush_interval": 5.0}})

    await bus.start()
    await storage.start()
    await aggregator.start()
    await scorer.start()
    await crawler.start()

    logger.info("Pipeline + storage running for 120 seconds...")
    await asyncio.sleep(120)

    await crawler.stop()
    await scorer.stop()
    await aggregator.stop()
    await storage.stop()
    await bus.stop()

    # Verify what landed in the database
    logger.info("=" * 60)
    logger.info(f"Database file: {get_db_path()}")
    async with aiosqlite.connect(get_db_path()) as db:
        async with db.execute("SELECT COUNT(*) FROM news") as cur:
            row = await cur.fetchone()
            logger.info(f"news rows:      {row[0]}")
        async with db.execute("SELECT COUNT(*) FROM sentiment") as cur:
            row = await cur.fetchone()
            logger.info(f"sentiment rows: {row[0]}")
        async with db.execute(
            "SELECT title, score, coins FROM sentiment ORDER BY id DESC LIMIT 5"
        ) as cur:
            logger.info("Most recent 5 sentiment rows:")
            async for r in cur:
                logger.info(f"  [{r[1]:+.2f}] {r[2]}  {r[0][:60]}")


if __name__ == "__main__":
    asyncio.run(run_test())
