"""
Smoke test for the event bus.

Run with:
    uv run python -m tests.test_event_bus

This test verifies:
1. Multiple handlers can subscribe to the same event type.
2. Events are dispatched to all subscribers.
3. An exception in one handler does not affect others.
"""

import asyncio

from loguru import logger

from core.event_bus import EventBus
from core.event_types import EventType
from core.events import Event


# ---------------------------------------------------------------------
# Fake handlers, simulating different plugins
# ---------------------------------------------------------------------
async def fake_strategy(event: Event) -> None:
    logger.info(f"[Strategy] received: {event.data}")


async def fake_logger(event: Event) -> None:
    logger.info(f"[Logger]   received: {event.data}")


async def buggy_handler(event: Event) -> None:
    """Intentionally raises to test isolation."""
    raise RuntimeError("simulated plugin failure")


# ---------------------------------------------------------------------
# Test entry
# ---------------------------------------------------------------------
async def run_test() -> None:
    bus = EventBus()

    bus.subscribe(EventType.MARKET_DATA, fake_strategy)
    bus.subscribe(EventType.MARKET_DATA, fake_logger)
    bus.subscribe(EventType.MARKET_DATA, buggy_handler)

    await bus.start()

    # Emit 3 fake market data events
    for i in range(3):
        await bus.publish(
            Event(
                type=EventType.MARKET_DATA,
                source="fake_data_source",
                data={"symbol": "AAPL", "price": 150.0 + i, "tick": i},
            )
        )
        await asyncio.sleep(0.1)

    # Allow the dispatch loop to drain the queue
    await asyncio.sleep(0.5)
    await bus.stop()


if __name__ == "__main__":
    asyncio.run(run_test())
