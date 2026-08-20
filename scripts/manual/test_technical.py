"""
End-to-end test for Module B (technical pipeline):

    Binance -> kline_fetcher -> KLINE -> indicator_engine -> TECHNICAL_SCORE

Run with:
    uv run python -m tests.test_technical

Runs for 40 seconds (long enough for at least one full fetch+compute
cycle) then prints the latest raw indicators per symbol.

Note: this prints RAW indicators only. Combining them into a trading
signal is a strategy decision handled in a separate layer.
"""

import asyncio

from loguru import logger

from core.config import load_config
from core.event_bus import EventBus
from core.event_types import EventType
from core.events import Event
from plugins.technical.kline_fetcher import KlineFetcher
from plugins.technical.indicator_engine import IndicatorEngine

_latest: dict[str, dict] = {}


async def _capture(event: Event) -> None:
    _latest[event.data["symbol"]] = event.data


async def run_test() -> None:
    config = load_config()
    bus = EventBus()

    bus.subscribe(EventType.TECHNICAL_SCORE, _capture)

    fetcher = KlineFetcher(bus, config=config["technical"])
    engine = IndicatorEngine(bus, config=config)

    await bus.start()
    await engine.start()
    await fetcher.start()

    logger.info("Module B pipeline running for 40 seconds...")
    await asyncio.sleep(40)

    await fetcher.stop()
    await engine.stop()
    await bus.stop()

    logger.info("=" * 78)
    logger.info("LATEST RAW INDICATORS (no weighting applied):")
    for symbol, d in _latest.items():
        logger.info(
            f"  {symbol:10s} close=${d['close_price']:.2f}"
        )
        logger.info(
            f"    momentum={d['momentum_score']:+.2f}  "
            f"rsi={d['rsi']:.1f}  "
            f"vol_ratio={d['volume_ratio']:.2f}"
        )
        logger.info(
            f"    px/ema45={d['price_vs_ema45']:+.1f}%  "
            f"px/ema125={d['price_vs_ema125']:+.1f}%  "
            f"ema45/125={d['ema45_vs_ema125']:+.1f}%"
        )


if __name__ == "__main__":
    asyncio.run(run_test())
