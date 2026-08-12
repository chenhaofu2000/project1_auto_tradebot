"""
Storage plugin.

Subscribes to NEWS, SENTIMENT_SCORE and TECHNICAL_SCORE events and
persists them to SQLite. This is the foundation for backtesting:
without stored history, past signals cannot be replayed or evaluated.

Sentiment history in particular cannot be backfilled later -- RSS feeds
only expose the most recent ~30 articles -- so it must be captured live.
Technical indicators could in principle be recomputed from historical
K-lines, but storing them keeps both factor streams time-aligned.

Writes are buffered and flushed periodically rather than one
transaction per event.
"""

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

import aiosqlite
from loguru import logger

from core.database import get_db_path
from core.event_bus import EventBus
from core.event_types import EventType
from core.events import Event
from core.plugin import Plugin


class StoragePlugin(Plugin):
    """Persists news, sentiment and technical events to SQLite."""

    name = "storage"

    def __init__(self, bus: EventBus, config: dict[str, Any] | None = None) -> None:
        super().__init__(bus, config)
        storage_cfg = self.config.get("storage", {})
        self.flush_interval: float = float(storage_cfg.get("flush_interval", 5.0))

        self._news_buffer: list[Event] = []
        self._sentiment_buffer: list[Event] = []
        self._technical_buffer: list[Event] = []
        self._db: aiosqlite.Connection | None = None
        self._task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    async def start(self) -> None:
        self._running = True
        self._db = await aiosqlite.connect(get_db_path())

        self.bus.subscribe(EventType.NEWS, self._on_news)
        self.bus.subscribe(EventType.SENTIMENT_SCORE, self._on_sentiment)
        self.bus.subscribe(EventType.TECHNICAL_SCORE, self._on_technical)

        self._task = asyncio.create_task(self._flush_loop())
        logger.info(f"[{self.name}] started, writing to {get_db_path()}")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        # Final flush so nothing buffered is lost on shutdown.
        await self._flush()
        if self._db:
            await self._db.close()
        logger.info(f"[{self.name}] stopped")

    # ------------------------------------------------------------------
    async def _on_news(self, event: Event) -> None:
        self._news_buffer.append(event)

    async def _on_sentiment(self, event: Event) -> None:
        self._sentiment_buffer.append(event)

    async def _on_technical(self, event: Event) -> None:
        self._technical_buffer.append(event)

    async def _flush_loop(self) -> None:
        while self._running:
            await asyncio.sleep(self.flush_interval)
            await self._flush()

    async def _flush(self) -> None:
        """Write buffered events to SQLite, one transaction per table."""
        if not self._db:
            return

        # --- news ---
        if self._news_buffer:
            batch, self._news_buffer = self._news_buffer, []
            await self._db.executemany(
                """INSERT INTO news
                   (article_id, feed, title, link, published, received_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                [
                    (
                        ev.data.get("id", ""),
                        ev.data.get("feed", ""),
                        ev.data.get("title", ""),
                        ev.data.get("link", ""),
                        ev.data.get("published", ""),
                        ev.timestamp.isoformat(),
                    )
                    for ev in batch
                ],
            )
            await self._db.commit()
            logger.debug(f"[{self.name}] flushed {len(batch)} news rows")

        # --- sentiment ---
        if self._sentiment_buffer:
            batch, self._sentiment_buffer = self._sentiment_buffer, []
            await self._db.executemany(
                """INSERT INTO sentiment
                   (title, feed, link, coins, score, reason, scored_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        ev.data.get("title", ""),
                        ev.data.get("feed", ""),
                        ev.data.get("link", ""),
                        json.dumps(ev.data.get("coins", []), ensure_ascii=False),
                        ev.data.get("score", 0.0),
                        ev.data.get("reason", ""),
                        ev.timestamp.isoformat(),
                    )
                    for ev in batch
                ],
            )
            await self._db.commit()
            logger.debug(f"[{self.name}] flushed {len(batch)} sentiment rows")

        # --- technical ---
        if self._technical_buffer:
            batch, self._technical_buffer = self._technical_buffer, []
            await self._db.executemany(
                """INSERT INTO technical
                   (symbol, momentum_score, rsi, volume_ratio,
                    price_vs_ema45, price_vs_ema125, ema45_vs_ema125,
                    ema45, ema125, close_price, computed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        ev.data.get("symbol", ""),
                        ev.data.get("momentum_score"),
                        ev.data.get("rsi"),
                        ev.data.get("volume_ratio"),
                        ev.data.get("price_vs_ema45"),
                        ev.data.get("price_vs_ema125"),
                        ev.data.get("ema45_vs_ema125"),
                        ev.data.get("ema45"),
                        ev.data.get("ema125"),
                        ev.data.get("close_price"),
                        ev.timestamp.isoformat(),
                    )
                    for ev in batch
                ],
            )
            await self._db.commit()
            logger.debug(f"[{self.name}] flushed {len(batch)} technical rows")

    # ------------------------------------------------------------------
    async def save_snapshot(
        self, coin: str, avg_score: float, mentions: int, is_hot: bool
    ) -> None:
        """Record a rolling-aggregator snapshot for time-series analysis."""
        if not self._db:
            return
        await self._db.execute(
            """INSERT INTO snapshots (coin, avg_score, mentions, is_hot, captured_at)
               VALUES (?, ?, ?, ?, ?)""",
            (
                coin,
                avg_score,
                mentions,
                int(is_hot),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        await self._db.commit()