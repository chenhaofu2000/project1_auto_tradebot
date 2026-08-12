"""
Async event bus implementation.

Design notes:
- Pub/sub pattern: modules subscribe to event types, anyone can publish.
- All dispatch is async; a slow subscriber does not block publishers.
- Subscriber exceptions are caught and logged, never propagated.
  This is critical for plugin isolation: one buggy plugin must not
  crash the whole system.
"""

import asyncio
from collections import defaultdict

from loguru import logger

from core.event_types import EventType
from core.events import Event
from core.handlers import AsyncEventHandler


class EventBus:
    """Asynchronous publish/subscribe event bus."""

    def __init__(self) -> None:
        # event_type -> list of handlers subscribed to it
        self._subscribers: dict[EventType, list[AsyncEventHandler]] = defaultdict(list)

        # Internal queue. Publishers push, the dispatch loop pops.
        self._queue: asyncio.Queue[Event] = asyncio.Queue()

        self._running: bool = False
        self._dispatch_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Subscription API
    # ------------------------------------------------------------------
    def subscribe(
        self, event_type: EventType, handler: AsyncEventHandler
    ) -> None:
        """Register a handler for a given event type."""
        self._subscribers[event_type].append(handler)
        logger.debug(
            f"Subscribed handler '{handler.__name__}' to '{event_type.value}'"
        )

    def unsubscribe(
        self, event_type: EventType, handler: AsyncEventHandler
    ) -> None:
        """Remove a previously registered handler. Useful when hot-reloading
        a strategy plugin during development."""
        if handler in self._subscribers[event_type]:
            self._subscribers[event_type].remove(handler)
            logger.debug(
                f"Unsubscribed handler '{handler.__name__}' from '{event_type.value}'"
            )

    # ------------------------------------------------------------------
    # Publish API
    # ------------------------------------------------------------------
    async def publish(self, event: Event) -> None:
        """Publish an event. Returns immediately; dispatch happens in
        the background loop."""
        await self._queue.put(event)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def start(self) -> None:
        """Start the background dispatch loop."""
        if self._running:
            return
        self._running = True
        self._dispatch_task = asyncio.create_task(self._dispatch_loop())
        logger.info("EventBus started")

    async def stop(self) -> None:
        """Stop the bus and wait for the dispatch loop to exit cleanly."""
        self._running = False
        if self._dispatch_task is not None:
            await self._dispatch_task
            self._dispatch_task = None
        logger.info("EventBus stopped")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------
    async def _dispatch_loop(self) -> None:
        """Continuously pull events from the queue and dispatch to
        subscribers. Wakes up at least once per second to re-check the
        running flag (so stop() is responsive even when the queue is idle)."""
        logger.info("Dispatch loop running")
        while self._running:
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            await self._dispatch_single(event)

        logger.info("Dispatch loop exited")

    async def _dispatch_single(self, event: Event) -> None:
        """Dispatch one event to all of its subscribers concurrently.
        Exceptions are caught per-handler so one failure does not affect
        other handlers."""
        handlers = self._subscribers.get(event.type, [])
        if not handlers:
            logger.debug(
                f"No subscribers for '{event.type.value}', event dropped"
            )
            return

        results = await asyncio.gather(
            *(handler(event) for handler in handlers),
            return_exceptions=True,
        )

        for handler, result in zip(handlers, results):
            if isinstance(result, Exception):
                logger.error(
                    f"Handler '{handler.__name__}' raised on "
                    f"'{event.type.value}': {result!r}"
                )
