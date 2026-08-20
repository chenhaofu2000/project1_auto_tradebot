"""
Smoke test for the news crawler plugin.

Run with:
    uv run python -m tests.test_news_crawler

What this test does:
1. Spins up an EventBus.
2. Subscribes a print-handler to NEWS events.
3. Starts the news crawler with a short poll interval.
4. Lets it run for ~25 seconds, then shuts down.

If everything works, you should see real headlines from CoinDesk and
CoinTelegraph printed to the terminal.
"""

import asyncio

from loguru import logger

from core.event_bus import EventBus
from core.event_types import EventType
from core.events import Event
from plugins.sentiment.news_crawler import NewsCrawler


async def print_news(event: Event) -> None:
    """Just print incoming news. Later this will be replaced by the
    sentiment scoring plugin."""
    title = event.data.get("title", "<no title>")
    feed = event.data.get("feed", "<unknown>")
    logger.info(f"[NEWS] ({feed}) {title}")


async def run_test() -> None:
    bus = EventBus()
    bus.subscribe(EventType.NEWS, print_news)

    crawler = NewsCrawler(
        bus,
        config={
            "poll_interval": 20.0,   # Poll every 20 seconds for this test
            "request_timeout": 10.0,
        },
    )

    await bus.start()
    await crawler.start()

    logger.info("Crawler running. Waiting 25 seconds for headlines...")
    await asyncio.sleep(25)

    await crawler.stop()
    await bus.stop()


if __name__ == "__main__":
    asyncio.run(run_test())
