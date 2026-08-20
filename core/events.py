"""
Event data structures.

Every message on the event bus is an Event instance. The `data` field
is intentionally a free-form dict so different event types can carry
different payloads without forcing rigid schemas at this layer.

If you later want strict schemas per event type, create subclasses
of Event here (e.g. MarketDataEvent with typed price/volume fields).
"""

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from core.event_types import EventType


def _utc_now() -> datetime:
    """Factory for UTC timestamps. All events use UTC for consistency
    between backtest and live trading."""
    return datetime.now(UTC)


class Event(BaseModel):
    """Base event class. All bus messages are instances of this."""

    type: EventType
    timestamp: datetime = Field(default_factory=_utc_now)
    source: str = "unknown"  # Which module emitted this event
    data: dict[str, Any] = Field(default_factory=dict)

    def __str__(self) -> str:
        return f"[{self.timestamp.isoformat()}] {self.type.value} from {self.source}: {self.data}"
