"""
Range-trading strategy logic -- THIS IS YOURS TO TUNE.

Your stated logic, encoded:

  1. Detect regime: is the market ranging or trending?
     - Ranging  = EMA45 and EMA125 are close together (tangled).
     - Trending = EMAs clearly separated.
  2. If TRENDING -> do nothing (flat). Trends are left for manual
     handling by design; auto-trading only harvests ranges.
  3. If RANGING -> mean-reversion:
     - price near a SUPPORT  -> LONG  (buy low)
     - price near a RESISTANCE -> SHORT (sell high)

This is a scaffold, not a proven strategy. Whether ranging BTC actually
pays under these rules is exactly what the backtester must tell you.
All thresholds live in config.yaml -> strategy.

Signal: dict with 'direction' (LONG/SHORT/FLAT), a score in [-1,1], and
a 'reason' so you can see why.
"""

from typing import Any


def compute_signal(
    factors: dict[str, Any],
    level_info: dict[str, Any],
    cfg: dict[str, Any],
) -> dict[str, Any]:
    """
    factors: raw indicators (ema45_vs_ema125, rsi, ...)
    level_info: output of levels.nearest_levels()
    cfg: strategy config section
    """
    # --- Step 1: regime detection ---
    ema_spread = abs(factors.get("ema45_vs_ema125", 0.0))
    range_max_spread = float(cfg.get("range_max_ema_spread_pct", 1.0))
    is_ranging = ema_spread <= range_max_spread

    if not is_ranging:
        return _flat(f"trending (EMA spread {ema_spread:.2f}% > {range_max_spread}%), staying out")

    # --- Step 2: in a range -> mean reversion around levels ---
    near_pct = float(cfg.get("near_level_pct", 0.8))
    dist_sup = level_info.get("dist_to_support_pct")
    dist_res = level_info.get("dist_to_resistance_pct")

    near_support = dist_sup is not None and dist_sup <= near_pct
    near_resistance = dist_res is not None and dist_res <= near_pct

    # Optional RSI confirmation: only go long if also oversold, short if
    # also overbought. Set require_rsi_confirm: false to disable.
    rsi = factors.get("rsi", 50.0)
    rsi_oversold = float(cfg.get("rsi_oversold", 35.0))
    rsi_overbought = float(cfg.get("rsi_overbought", 65.0))
    require_rsi = bool(cfg.get("require_rsi_confirm", True))

    if near_support and (not require_rsi or rsi <= rsi_oversold):
        # Closer to support = stronger. Score scales with proximity.
        strength = 1.0 - min(dist_sup / near_pct, 1.0)
        return {
            "direction": "LONG",
            "score": round(strength, 3),
            "reason": (f"ranging; near support ({dist_sup:.2f}% away), rsi={rsi:.0f}"),
        }

    if near_resistance and (not require_rsi or rsi >= rsi_overbought):
        strength = 1.0 - min(dist_res / near_pct, 1.0)
        return {
            "direction": "SHORT",
            "score": round(-strength, 3),
            "reason": (f"ranging; near resistance ({dist_res:.2f}% away), rsi={rsi:.0f}"),
        }

    return _flat(f"ranging but price mid-range (sup {dist_sup}, res {dist_res})")


def _flat(reason: str) -> dict[str, Any]:
    return {"direction": "FLAT", "score": 0.0, "reason": reason}
