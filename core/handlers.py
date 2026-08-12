"""
Handler type definitions.

Centralizing handler signatures here makes it easy to evolve the
contract (e.g. add priority, add sync handlers) without hunting through
the codebase.
"""

from typing import Awaitable, Callable

from core.events import Event


# An async handler: receives an Event, returns an awaitable.
# All handlers are async by default to keep the bus non-blocking.
AsyncEventHandler = Callable[[Event], Awaitable[None]]
