"""
Sentiment aggregator plugin.

Subscribes to SENTIMENT_SCORE events and maintains, per coin, a rolling
window of (timestamp, score) samples. From this it derives:

- avg_score:   mean sentiment over the window
- mentions:    how many news items mentioned the coin in the window
- is_hot:      mention count >= threshold (hype detection for small coins)

This rolling state is what the future trading module will consume.
"""

import time
from collections import defaultdict, deque
from typing import Any

from loguru import logger

from core.event_bus import EventBus
from core.event_types import EventType
from core.events import Event
from core.plugin import Plugin


class SentimentAggregator(Plugin):
    """Rolling per-coin sentiment state."""

    name = "sentiment_aggregator"

    def __init__(self, bus: EventBus, config: dict[str, Any] | None = None) -> None:
        super().__init__(bus, config)

        sent_cfg = self.config.get("sentiment", {})
        self.window_seconds: float = float(sent_cfg.get("window_minutes", 60)) * 60
        self.hot_threshold: int = int(sent_cfg.get("hot_mention_threshold", 3))
        self.core_coins: list[str] = sent_cfg.get("core_coins", ["BTC", "ETH"])

        # coin -> deque of (unix_ts, score)
        self._samples: dict[str, deque[tuple[float, float]]] = defaultdict(deque)

    # ------------------------------------------------------------------
    async def start(self) -> None:
        self._running = True
        self.bus.subscribe(EventType.SENTIMENT_SCORE, self._on_score)
        logger.info(f"[{self.name}] started (window={self.window_seconds}s)")

    async def stop(self) -> None:
        self._running = False
        logger.info(f"[{self.name}] stopped")

    # ------------------------------------------------------------------
    async def _on_score(self, event: Event) -> None:
        now = time.time()
        score = float(event.data.get("score", 0.0))
        coins = event.data.get("coins", [])

        for coin in coins:
            self._samples[coin].append((now, score))
            self._evict_old(coin, now)

            snap = self.snapshot(coin)
            hot = " HOT!" if snap["is_hot"] and coin not in self.core_coins else ""
            logger.info(
                f"[{self.name}] {coin}: avg={snap['avg_score']:+.2f} "
                f"mentions={snap['mentions']}{hot}"
            )

    def _evict_old(self, coin: str, now: float) -> None:
        dq = self._samples[coin]
        cutoff = now - self.window_seconds
        while dq and dq[0][0] < cutoff:
            dq.popleft()

    # ------------------------------------------------------------------
    def snapshot(self, coin: str) -> dict[str, Any]:
        """Current rolling state for one coin. The trading module will
        call this (or a future periodic SENTIMENT_SNAPSHOT event)."""
        dq = self._samples.get(coin, deque())
        n = len(dq)
        avg = sum(s for _, s in dq) / n if n else 0.0
        return {
            "coin": coin,
            "avg_score": avg,
            "mentions": n,
            "is_hot": n >= self.hot_threshold,
        }

    def all_snapshots(self) -> list[dict[str, Any]]:
        return [self.snapshot(c) for c in self._samples]
