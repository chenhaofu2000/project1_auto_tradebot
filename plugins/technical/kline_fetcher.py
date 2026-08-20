"""
K-line (OHLCV) fetcher plugin.

Periodically pulls recent candlestick data from Binance's public REST
API (no API key required for market data) and publishes it as KLINE
events, one per symbol per poll.

Design note: each poll re-fetches a fresh window of `lookback` candles
rather than incrementally maintaining state across polls. This trades
a small amount of bandwidth for correctness -- the indicator engine
always computes from a complete, self-consistent window instead of
patching together partial updates. Given the tiny payload size, this
cost is negligible.
"""

import asyncio
from typing import Any

import aiohttp
from loguru import logger

from core.event_bus import EventBus
from core.event_types import EventType
from core.events import Event
from core.plugin import Plugin

BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"


class KlineFetcher(Plugin):
    """Polls Binance public REST API for recent candlestick data."""

    name = "kline_fetcher"

    def __init__(self, bus: EventBus, config: dict[str, Any] | None = None) -> None:
        super().__init__(bus, config)

        self.symbols: list[str] = self.config.get("symbols", ["BTCUSDT", "ETHUSDT"])
        self.interval: str = self.config.get("interval", "1h")
        self.lookback: int = int(self.config.get("lookback", 100))
        self.poll_interval: float = float(self.config.get("poll_interval", 300.0))
        self.request_timeout: float = float(self.config.get("request_timeout", 10.0))

        self._task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info(
            f"[{self.name}] started, polling {self.symbols} "
            f"every {self.poll_interval}s (interval={self.interval})"
        )

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
    async def _poll_loop(self) -> None:
        async with aiohttp.ClientSession() as session:
            while self._running:
                try:
                    await self._fetch_all_symbols(session)
                except Exception as e:
                    logger.exception(f"[{self.name}] poll cycle failed: {e}")

                for _ in range(int(self.poll_interval * 10)):
                    if not self._running:
                        return
                    await asyncio.sleep(0.1)

    async def _fetch_all_symbols(self, session: aiohttp.ClientSession) -> None:
        results = await asyncio.gather(
            *(self._fetch_one_symbol(session, sym) for sym in self.symbols),
            return_exceptions=True,
        )
        for sym, result in zip(self.symbols, results, strict=True):
            if isinstance(result, Exception):
                logger.warning(f"[{self.name}] symbol '{sym}' failed: {result!r}")

    async def _fetch_one_symbol(self, session: aiohttp.ClientSession, symbol: str) -> None:
        params = {
            "symbol": symbol,
            "interval": self.interval,
            "limit": self.lookback,
        }
        timeout = aiohttp.ClientTimeout(total=self.request_timeout)
        async with session.get(BINANCE_KLINES_URL, params=params, timeout=timeout) as resp:
            resp.raise_for_status()
            raw = await resp.json()

        # Each raw candle: [open_time, open, high, low, close, volume, close_time, ...]
        candles = [
            {
                "open_time": c[0],
                "open": float(c[1]),
                "high": float(c[2]),
                "low": float(c[3]),
                "close": float(c[4]),
                "volume": float(c[5]),
            }
            for c in raw
        ]

        await self.bus.publish(
            Event(
                type=EventType.KLINE,
                source=self.name,
                data={
                    "symbol": symbol,
                    "interval": self.interval,
                    "candles": candles,
                },
            )
        )
        logger.info(f"[{self.name}] {symbol}: fetched {len(candles)} candles")
