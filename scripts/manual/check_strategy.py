"""
Test support/resistance detection and range-strategy signals against
real historical K-lines from the database.

Run with:
    uv run python -m tests.test_strategy

Prints detected levels for BTCUSDT and the signal at the latest bar,
so you can eyeball whether the levels land where you'd draw them.
"""

import sqlite3
from pathlib import Path

from loguru import logger

from core.config import load_config
from plugins.strategy.levels import find_levels, nearest_levels
from plugins.strategy.weights import compute_signal

DB = Path(__file__).resolve().parent.parent / "data" / "tradebot.db"


def load_candles(symbol: str, interval: str, limit: int) -> list[dict]:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT open_time, open, high, low, close, volume FROM klines
           WHERE symbol=? AND interval=? ORDER BY open_time DESC LIMIT ?""",
        (symbol, interval, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in reversed(rows)]


def run() -> None:
    config = load_config()
    strat_cfg = config["strategy"]

    candles = load_candles("BTCUSDT", "4h", 360)
    if not candles:
        logger.error("No BTCUSDT 1h candles in DB. Run download_history first.")
        return

    logger.info(f"Loaded {len(candles)} candles")

    # Detect levels using everything EXCEPT the last bar (that's 'now').
    history = candles[:-1]
    current = candles[-1]

    levels = find_levels(history, strat_cfg["levels"])

    logger.info("=" * 55)
    logger.info("RESISTANCE levels (strongest first):")
    for lv in levels["resistance"]:
        logger.info(f"  ${lv['price']:>10.2f}  ({lv['touches']} touches)")
    logger.info("SUPPORT levels (strongest first):")
    for lv in levels["support"]:
        logger.info(f"  ${lv['price']:>10.2f}  ({lv['touches']} touches)")

    logger.info("=" * 55)
    logger.info(f"Current price: ${current['close']:.2f}")

    ni = nearest_levels(current["close"], levels)
    if ni["nearest_support"]:
        logger.info(
            f"Nearest support:    ${ni['nearest_support']['price']:.2f} "
            f"({ni['dist_to_support_pct']:.2f}% below)"
        )
    if ni["nearest_resistance"]:
        logger.info(
            f"Nearest resistance: ${ni['nearest_resistance']['price']:.2f} "
            f"({ni['dist_to_resistance_pct']:.2f}% above)"
        )

    # Build a minimal factors dict from the candles for the signal.
    # (In production these come from IndicatorEngine; here we approximate
    # ema spread + rsi just to exercise the signal logic.)
    from plugins.technical.indicator_engine import _compute_ema, _compute_rsi

    closes = [c["close"] for c in history]
    ema45 = _compute_ema(closes, 45)
    ema125 = _compute_ema(closes, 125)
    factors = {
        "ema45_vs_ema125": (ema45 - ema125) / ema125 * 100 if ema125 else 0.0,
        "rsi": _compute_rsi(closes, 14),
    }

    signal = compute_signal(factors, ni, strat_cfg)
    logger.info("=" * 55)
    logger.info(f"EMA spread: {factors['ema45_vs_ema125']:+.2f}%  RSI: {factors['rsi']:.0f}")
    logger.info(f"SIGNAL: {signal['direction']}  score={signal['score']}")
    logger.info(f"Reason: {signal['reason']}")


if __name__ == "__main__":
    run()
