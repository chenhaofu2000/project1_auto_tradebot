"""
Event type enumeration.

Keep this file isolated so adding a new event type does not require
touching any other module. To add a new event type, just append a new
member here, then any plugin can subscribe to it.
"""

from enum import Enum


class EventType(str, Enum):
    """All event types flowing through the system."""

    # --- Market data ---
    MARKET_DATA = "market_data"          # Real-time price / volume / tick
    KLINE = "kline"                      # Candlestick data (OHLCV)

    # --- Strategy / scoring layer ---
    SIGNAL = "signal"                    # Buy / sell signal
    SENTIMENT_SCORE = "sentiment_score"  # Output of sentiment module (A)
    TECHNICAL_SCORE = "technical_score"  # Output of technical module (B)

    # --- Execution layer ---
    ORDER = "order"                      # Order to be sent to broker
    FILL = "fill"                        # Order fill confirmation

    # --- External information feeds ---
    NEWS = "news"                        # News article from RSS / API
    SOCIAL_POST = "social_post"          # Raw social media post (X, Reddit)

    # --- System events ---
    SYSTEM = "system"                    # Startup, shutdown, errors
