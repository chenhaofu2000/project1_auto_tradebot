"""
Support / resistance level detection.

Identifies price levels that have been touched multiple times -- the
programmatic version of drawing horizontal lines on a chart where price
repeatedly bounced.

CRITICAL -- no look-ahead:
Detecting a "local high" normally needs to see the candles AFTER it
(to confirm nothing went higher). In a backtest you are standing at the
present and cannot see the future. Every function here takes only the
candles UP TO the current point and confirms pivots using a lookback
window that sits ENTIRELY in the past. Using future candles here would
make the backtest look brilliant and fail instantly in live trading.

Everything is parameterized via the 'strategy.levels' config section so
you can tune detection without touching code.
"""

from typing import Any


def _is_pivot_high(highs: list[float], i: int, left: int, right: int) -> bool:
    """True if bar i is a local high: strictly higher than `left` bars
    before it and `right` bars after it. Caller guarantees i-left >= 0
    and i+right < len, so this never peeks past what's allowed."""
    pivot = highs[i]
    for j in range(i - left, i):
        if highs[j] >= pivot:
            return False
    for j in range(i + 1, i + right + 1):
        if highs[j] >= pivot:
            return False
    return True


def _is_pivot_low(lows: list[float], i: int, left: int, right: int) -> bool:
    pivot = lows[i]
    for j in range(i - left, i):
        if lows[j] <= pivot:
            return False
    for j in range(i + 1, i + right + 1):
        if lows[j] <= pivot:
            return False
    return True


def find_levels(candles: list[dict[str, Any]], cfg: dict[str, Any]) -> dict[str, Any]:
    """Find support and resistance levels from a list of candles.

    IMPORTANT: `candles` must contain ONLY bars up to (and including) the
    current moment. The caller is responsible for not passing future
    bars. This function then confirms each pivot using `right` bars that
    are still in the past relative to the caller's 'now'.

    Returns support/resistance levels sorted by strength (touch count).
    """
    left = int(cfg.get("pivot_left", 3))
    right = int(cfg.get("pivot_right", 3))
    tolerance_pct = float(cfg.get("cluster_tolerance_pct", 0.5))
    min_touches = int(cfg.get("min_touches", 2))
    max_levels = int(cfg.get("max_levels", 6))

    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    n = len(candles)

    # A pivot at bar i needs `right` confirmed bars after it. Since we
    # only ever look within `candles`, the last `right` bars can't be
    # confirmed as pivots yet -- which is correct, they're too recent.
    raw_res: list[float] = []
    raw_sup: list[float] = []
    for i in range(left, n - right):
        if _is_pivot_high(highs, i, left, right):
            raw_res.append(highs[i])
        if _is_pivot_low(lows, i, left, right):
            raw_sup.append(lows[i])

    resistance = _cluster(raw_res, tolerance_pct, min_touches)
    support = _cluster(raw_sup, tolerance_pct, min_touches)

    # Keep the strongest (most-touched) levels only.
    resistance = sorted(resistance, key=lambda d: d["touches"], reverse=True)[:max_levels]
    support = sorted(support, key=lambda d: d["touches"], reverse=True)[:max_levels]

    return {"support": support, "resistance": resistance}


def _cluster(prices: list[float], tolerance_pct: float, min_touches: int) -> list[dict]:
    """Group nearby pivot prices into a single level. Two pivots within
    tolerance_pct of each other count as touches of the same level.
    A level must have at least `min_touches` to be kept."""
    if not prices:
        return []

    prices = sorted(prices)
    clusters: list[list[float]] = [[prices[0]]]

    for p in prices[1:]:
        anchor = clusters[-1][0]
        if abs(p - anchor) / anchor * 100 <= tolerance_pct:
            clusters[-1].append(p)
        else:
            clusters.append([p])

    levels = []
    for group in clusters:
        if len(group) >= min_touches:
            levels.append({
                "price": sum(group) / len(group),   # average of the touches
                "touches": len(group),
            })
    return levels


def nearest_levels(current_price: float, levels: dict[str, Any]) -> dict[str, Any]:
    """Given current price and detected levels, find the nearest support
    below and nearest resistance above, plus how far away they are (%).

    'Distance' is what the strategy uses: price near a support = candidate
    long; price near a resistance = candidate short.
    """
    supports = [lv for lv in levels["support"] if lv["price"] < current_price]
    resistances = [lv for lv in levels["resistance"] if lv["price"] > current_price]

    nearest_sup = max(supports, key=lambda d: d["price"], default=None)
    nearest_res = min(resistances, key=lambda d: d["price"], default=None)

    out: dict[str, Any] = {
        "nearest_support": nearest_sup,
        "nearest_resistance": nearest_res,
        "dist_to_support_pct": None,
        "dist_to_resistance_pct": None,
    }
    if nearest_sup:
        out["dist_to_support_pct"] = (
            (current_price - nearest_sup["price"]) / current_price * 100
        )
    if nearest_res:
        out["dist_to_resistance_pct"] = (
            (nearest_res["price"] - current_price) / current_price * 100
        )
    return out