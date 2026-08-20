"""
End-to-end test for Module A (sentiment pipeline):

    RSS -> news_crawler -> NEWS -> scorer (LLM) -> SENTIMENT_SCORE -> aggregator

Run with:
    uv run python -m tests.test_sentiment

Requires a valid API key in config.yaml. Runs ~60s then prints a summary.
"""

import asyncio

from loguru import logger

from core.config import load_config
from core.event_bus import EventBus
from plugins.sentiment.aggregator import SentimentAggregator
from plugins.sentiment.news_crawler import NewsCrawler
from plugins.sentiment.scorer import SentimentScorer


async def run_test() -> None:
    config = load_config()
    bus = EventBus()

    crawler = NewsCrawler(
        bus,
        config={
            "poll_interval": 30.0,
            "request_timeout": config["news_crawler"]["request_timeout"],
        },
    )
    scorer = SentimentScorer(bus, config=config)
    aggregator = SentimentAggregator(bus, config=config)

    await bus.start()
    await aggregator.start()
    await scorer.start()
    await crawler.start()

    logger.info("Module A pipeline running for 60 seconds...")
    await asyncio.sleep(60)

    await crawler.stop()
    await scorer.stop()
    await aggregator.stop()
    await bus.stop()

    # Final summary
    logger.info("=" * 60)
    logger.info("FINAL SENTIMENT SNAPSHOT:")
    for snap in sorted(aggregator.all_snapshots(), key=lambda s: s["mentions"], reverse=True):
        hot = " <-- HOT" if snap["is_hot"] else ""
        logger.info(
            f"  {snap['coin']:8s} avg={snap['avg_score']:+.2f} mentions={snap['mentions']}{hot}"
        )


if __name__ == "__main__":
    asyncio.run(run_test())
