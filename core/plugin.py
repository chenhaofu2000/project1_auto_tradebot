"""
Plugin base class.

Every plugin (news crawler, sentiment scorer, technical analyzer,
trading bot, etc.) inherits from this class. The base class defines
a uniform lifecycle so the bootstrap layer can start/stop all
plugins the same way.

Lifecycle:
    __init__(bus, config)  -> register subscriptions
    await start()          -> begin background work (e.g. periodic tasks)
    await stop()           -> clean shutdown
"""

from abc import ABC, abstractmethod
from typing import Any

from loguru import logger

from core.event_bus import EventBus


class Plugin(ABC):
    """Abstract base for all plugins."""

    # Subclasses should override this with a unique, human-readable name.
    name: str = "unnamed_plugin"

    def __init__(self, bus: EventBus, config: dict[str, Any] | None = None) -> None:
        self.bus = bus
        self.config = config or {}
        self._running = False

    @abstractmethod
    async def start(self) -> None:
        """Begin the plugin's work. Subclasses MUST implement this.

        Typical patterns:
        - Subscribe to events on self.bus
        - Spawn a background task (e.g. periodic crawler)
        - Open network connections
        """
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Clean shutdown. Subclasses MUST implement this.

        Typical patterns:
        - Cancel background tasks
        - Close network connections
        - Unsubscribe from events
        """
        ...

    @property
    def is_running(self) -> bool:
        return self._running
