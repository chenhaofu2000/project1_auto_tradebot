"""
Technical indicator engine.

Subscribes to KLINE events and publishes RAW, OBJECTIVE indicators as
TECHNICAL_SCORE events. It deliberately does NOT combine them into a
single score.

DESIGN BOUNDARY (important):
This module is a data pipeline, not a strategy. Deciding how to weight
momentum vs RSI vs volume vs EMA distance, and what thresholds trigger
a trade, is a strategy decision that belongs in a separate layer owned
by the user. Keeping weighting out of here means the strategy can be
rewritten, backtested, and tuned without touching data collection.

Indicators published per symbol:
- momentum_score:   normalized % price change over the lookback window
- rsi:              standard 14-period RSI, raw 0..100 value
- volume_ratio:     latest volume / rolling average volume
- price_vs_ema45:   % deviation of price from the fast EMA
- price_vs_ema125:  % deviation of price from the slow EMA
- ema45_vs_ema125:  % deviation of fast EMA from slow EMA
                    (positive = fast above slow, negative = fast below)
- ema45 / ema125:   raw EMA values
- close_price:      latest CLOSED close
"""

from typing import Any

from loguru import logger

from core.event_bus import EventBus
from core.event_types import EventType
from core.events import Event
from core.plugin import Plugin


def _compute_rsi(closes: list[float], period: int = 14) -> float:
    """Standard RSI over the given closing prices. Returns 50.0
    (neutral) if there isn't enough data yet."""
    if len(closes) < period + 1:
        return 50.0

    gains, losses = [], []
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))

    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _compute_ema(values: list[float], period: int) -> float:
    """Standard EMA over the given values. Falls back to a simple
    average if there isn't enough data for a full period yet."""
    if not values:
        return 0.0
    if len(values) < period:
        return sum(values) / len(values)

    multiplier = 2.0 / (period + 1)
    # Seed the EMA with the SMA of the first `period` values.
    ema = sum(values[:period]) / period
    for price in values[period:]:
        ema = (price - ema) * multiplier + ema
    return ema


def _clip(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


class IndicatorEngine(Plugin):
    """Computes raw technical indicators from K-line data."""

    name = "indicator_engine"

    def __init__(self, bus: EventBus, config: dict[str, Any] | None = None) -> None:
        super().__init__(bus, config)

        tech_cfg = self.config.get("technical", {})
        self.rsi_period: int = int(tech_cfg.get("rsi_period", 14))
        self.momentum_lookback: int = int(tech_cfg.get("momentum_lookback", 24))
        self.momentum_clip_pct: float = float(tech_cfg.get("momentum_clip_pct", 5.0))
        self.volume_avg_periods: int = int(tech_cfg.get("volume_avg_periods", 20))
        self.ema_fast: int = int(tech_cfg.get("ema_fast", 45))
        self.ema_slow: int = int(tech_cfg.get("ema_slow", 125))

    # ------------------------------------------------------------------
    async def start(self) -> None:
        self._running = True
        self.bus.subscribe(EventType.KLINE, self._on_kline)
        logger.info(
            f"[{self.name}] started (rsi={self.rsi_period}, ema={self.ema_fast}/{self.ema_slow})"
        )

    async def stop(self) -> None:
        self._running = False
        logger.info(f"[{self.name}] stopped")

    # ------------------------------------------------------------------
    async def _on_kline(self, event: Event) -> None:
        symbol = event.data.get("symbol", "")
        candles = event.data.get("candles", [])

        # Need one extra candle because the last one gets discarded below.
        if len(candles) < self.rsi_period + 2:
            logger.debug(f"[{self.name}] {symbol}: not enough candles yet")
            return

        # Drop the last candle: it is still forming and its volume/close
        # are incomplete. Using it would leak partial, in-progress data
        # into the indicators -- e.g. volume would always look low simply
        # because the current bar hasn't finished. This also keeps live
        # behaviour consistent with backtests, which only ever see
        # completed bars.
        closed_candles = candles[:-1]
        closes = [c["close"] for c in closed_candles]
        volumes = [c["volume"] for c in closed_candles]
        current_price = closes[-1]

        # Warn if the fetch window is too short for a meaningful slow EMA.
        if len(closes) < self.ema_slow:
            logger.warning(
                f"[{self.name}] {symbol}: only {len(closes)} closed candles but "
                f"ema_slow={self.ema_slow}; EMA125 will be approximated. "
                f"Consider raising technical.lookback in config.yaml."
            )

        # --- Momentum: % change over lookback window, normalized to [-1, 1] ---
        lookback = min(self.momentum_lookback, len(closes) - 1)
        price_change_pct = (current_price - closes[-1 - lookback]) / closes[-1 - lookback] * 100
        momentum_score = _clip(price_change_pct / self.momentum_clip_pct, -1.0, 1.0)

        # --- RSI: raw 0..100 value, no interpretation applied here ---
        rsi = _compute_rsi(closes, self.rsi_period)

        # --- Volume: latest closed bar vs rolling average of prior bars ---
        avg_periods = min(self.volume_avg_periods, len(volumes) - 1)
        avg_volume = sum(volumes[-avg_periods - 1 : -1]) / avg_periods
        volume_ratio = volumes[-1] / avg_volume if avg_volume > 0 else 1.0

        # --- EMA relationships: % deviations, sign carries direction ---
        ema_fast = _compute_ema(closes, self.ema_fast)
        ema_slow = _compute_ema(closes, self.ema_slow)

        price_vs_ema45 = (current_price - ema_fast) / ema_fast * 100 if ema_fast > 0 else 0.0
        price_vs_ema125 = (current_price - ema_slow) / ema_slow * 100 if ema_slow > 0 else 0.0
        ema45_vs_ema125 = (ema_fast - ema_slow) / ema_slow * 100 if ema_slow > 0 else 0.0

        logger.info(
            f"[{self.name}] {symbol}: momentum={momentum_score:+.2f} "
            f"rsi={rsi:.1f} vol_ratio={volume_ratio:.2f} "
            f"px/ema45={price_vs_ema45:+.1f}% px/ema125={price_vs_ema125:+.1f}% "
            f"ema45/125={ema45_vs_ema125:+.1f}%"
        )

        await self.bus.publish(
            Event(
                type=EventType.TECHNICAL_SCORE,
                source=self.name,
                data={
                    "symbol": symbol,
                    # --- raw indicators; no combined score on purpose ---
                    "momentum_score": momentum_score,
                    "rsi": rsi,
                    "volume_ratio": volume_ratio,
                    "price_vs_ema45": price_vs_ema45,
                    "price_vs_ema125": price_vs_ema125,
                    "ema45_vs_ema125": ema45_vs_ema125,
                    "ema45": ema_fast,
                    "ema125": ema_slow,
                    "close_price": current_price,
                },
            )
        )
