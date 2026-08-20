"""
Smoke test for the K-line fetcher (Module B, step 1).

This test ONLY checks that we can reach Binance's public API from
your network and that the response parses correctly. No API key
needed -- this is public market data.

Run with:
    uv run python -m tests.test_kline_fetcher
"""

import asyncio

from loguru import logger

from core.event_bus import EventBus
from core.event_types import EventType
from core.events import Event
from plugins.technical.kline_fetcher import KlineFetcher


async def print_kline(event: Event) -> None:
    symbol = event.data.get("symbol", "?")
    candles = event.data.get("candles", [])
    if not candles:
        logger.warning(f"[KLINE] {symbol}: no candles received")
        return
    latest = candles[-1]
    logger.info(
        f"[KLINE] {symbol}: {len(candles)} candles received. "
        f"Latest close=${latest['close']:.2f} volume={latest['volume']:.2f}"
    )


async def run_test() -> None:
    bus = EventBus()
    bus.subscribe(EventType.KLINE, print_kline)

    fetcher = KlineFetcher(
        bus,
        config={
            "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
            "interval": "1h",
            "lookback": 50,
            "poll_interval": 30.0,
            "request_timeout": 10.0,
        },
    )

    await bus.start()
    await fetcher.start()

    logger.info("Fetching K-lines. Waiting 10 seconds...")
    await asyncio.sleep(10)

    await fetcher.stop()
    await bus.stop()


if __name__ == "__main__":
    asyncio.run(run_test())
