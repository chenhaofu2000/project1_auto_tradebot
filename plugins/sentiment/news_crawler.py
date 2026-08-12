"""
News crawler plugin.

Periodically fetches news from a list of RSS feeds and publishes each
new article as a NEWS event on the bus.

Design notes:
- Stateless per cycle: we track seen-article IDs in memory to avoid
  duplicate publishes. For production you'd want persistent dedup
  (e.g. SQLite) so restarts don't re-emit old news.
- Each feed is fetched concurrently via aiohttp; parsing is done by
  feedparser (sync, but very fast on small payloads).
- All errors per-feed are isolated: one broken feed does not stop
  the others.
"""

import asyncio
from typing import Any

import aiohttp
import feedparser
from loguru import logger

from core.event_bus import EventBus
from core.event_types import EventType
from core.events import Event
from core.plugin import Plugin


# Default RSS sources. Override via config if you want.
DEFAULT_FEEDS: list[dict[str, str]] = [
    {
        "name": "CoinDesk",
        "url": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    },
    {
        "name": "CoinTelegraph",
        "url": "https://cointelegraph.com/rss",
    },
]


class NewsCrawler(Plugin):
    """RSS-based news crawler."""

    name = "news_crawler"

    def __init__(self, bus: EventBus, config: dict[str, Any] | None = None) -> None:
        super().__init__(bus, config)

        # Config knobs (with defaults)
        self.feeds: list[dict[str, str]] = self.config.get("feeds", DEFAULT_FEEDS)
        self.poll_interval: float = float(self.config.get("poll_interval", 60.0))
        self.request_timeout: float = float(self.config.get("request_timeout", 10.0))

        # Dedup: track article IDs already published this session.
        self._seen_ids: set[str] = set()

        # Background task handle
        self._task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info(f"[{self.name}] started, polling every {self.poll_interval}s")

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info(f"[{self.name}] stopped")

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------
    async def _poll_loop(self) -> None:
        """Continuously poll all feeds at the configured interval."""
        async with aiohttp.ClientSession() as session:
            while self._running:
                try:
                    await self._fetch_all_feeds(session)
                except Exception as e:
                    # Catch-all so the loop never dies.
                    logger.exception(f"[{self.name}] poll cycle failed: {e}")

                # Sleep in small chunks so stop() is responsive.
                for _ in range(int(self.poll_interval * 10)):
                    if not self._running:
                        return
                    await asyncio.sleep(0.1)

    async def _fetch_all_feeds(self, session: aiohttp.ClientSession) -> None:
        """Fetch every feed concurrently; per-feed errors are isolated."""
        results = await asyncio.gather(
            *(self._fetch_one_feed(session, feed) for feed in self.feeds),
            return_exceptions=True,
        )
        for feed, result in zip(self.feeds, results):
            if isinstance(result, Exception):
                logger.warning(
                    f"[{self.name}] feed '{feed['name']}' failed: {result!r}"
                )

    async def _fetch_one_feed(
        self, session: aiohttp.ClientSession, feed: dict[str, str]
    ) -> None:
        """Fetch and parse a single RSS feed, publish new articles."""
        url = feed["url"]
        feed_name = feed["name"]

        timeout = aiohttp.ClientTimeout(total=self.request_timeout)
        async with session.get(url, timeout=timeout) as resp:
            resp.raise_for_status()
            text = await resp.text()

        # feedparser is synchronous; small payloads make this acceptable.
        parsed = feedparser.parse(text)

        new_count = 0
        for entry in parsed.entries:
            article_id = entry.get("id") or entry.get("link") or entry.get("title", "")
            if not article_id or article_id in self._seen_ids:
                continue
            self._seen_ids.add(article_id)
            new_count += 1

            await self._publish_article(feed_name, entry)

        logger.info(
            f"[{self.name}] {feed_name}: {new_count} new / {len(parsed.entries)} total"
        )

    async def _publish_article(self, feed_name: str, entry: Any) -> None:
        """Wrap a parsed RSS entry into a NEWS event and publish it."""
        event = Event(
            type=EventType.NEWS,
            source=f"{self.name}:{feed_name}",
            data={
                "feed": feed_name,
                "title": entry.get("title", ""),
                "link": entry.get("link", ""),
                "summary": entry.get("summary", ""),
                "published": entry.get("published", ""),
                "id": entry.get("id", ""),
            },
        )
        await self.bus.publish(event)
