"""支撑压力位诊断分析(一次性研究脚本,不进生产管道)。

回答两个问题:
  Q1 通道诊断: 水平位模型还能不能用? 还是必须做倾斜通道?
  Q2 hazard 曲线: 一个位置被触碰 N 次后, 未来 K 根内突破的概率是多少?

严格因果: 任何 t 时刻的判断只使用 <= t 的数据。
枢轴点需要右侧确认根, 因此"触碰确认时刻" = 枢轴根 + 右确认窗口。

用法:
    uv run python -m scripts.analysis.analyze_levels
    uv run python -m scripts.analysis.analyze_levels --symbols BTCUSDT --tf 4h
"""

from __future__ import annotations

import argparse
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Literal

import numpy as np
import pandas as pd
from loguru import logger

PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
DB_PATH: Final[Path] = PROJECT_ROOT / "data" / "market.db"

# ---- 可调参数(全部集中在这里,便于敏感性测试)----
PIVOT_LEFT: Final[int] = 5          # 枢轴左侧确认根数
PIVOT_RIGHT: Final[int] = 5         # 枢轴右侧确认根数(决定确认延迟)
LEVEL_TOLERANCE: Final[float] = 0.015   # 归入同一位置的价格容差 (1.5%)
BREAKOUT_THRESHOLD: Final[float] = 0.010  # 判定突破的收盘越界幅度 (1.0%)
LOOKFORWARD_BARS: Final[tuple[int, ...]] = (10, 20, 40)  # 前瞻窗口
MA_PERIOD_DAYS: Final[int] = 200    # 宏观代理变量: 200日均线
MIN_TOUCHES: Final[int] = 2         # 构成一个"位置"的最少枢轴数

Side = Literal["resistance", "support"]


# --------------------------------------------------------------------------
# 数据加载
# --------------------------------------------------------------------------


def load_klines(db_path: Path, symbol: str, interval: str = "1h") -> pd.DataFrame:
    """从 klines_history 读取并按 open_time 排序。"""
    with sqlite3.connect(db_path) as conn:
        df = pd.read_sql_query(
            """
            SELECT open_time, open, high, low, close, volume
            FROM klines_history
            WHERE symbol = ? AND interval = ?
            ORDER BY open_time ASC
            """,
            conn,
            params=(symbol, interval),
        )

    if df.empty:
        raise ValueError(f"{symbol} {interval} 无数据,请先跑 fetch_history")

    df["dt"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    return df.set_index("dt")


def resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """1h -> 4h/1d 重采样。label/closed 都用 left, 与 Binance open_time 语义一致。"""
    out = df.resample(rule, label="left", closed="left").agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
    )
    return out.dropna()


# --------------------------------------------------------------------------
# 枢轴点识别
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Pivot:
    """一个枢轴点。

    idx:        枢轴所在的 bar 序号
    confirm_idx: 该枢轴被确认的 bar 序号 (idx + PIVOT_RIGHT)。因果分析必须用这个。
    """

    idx: int
    confirm_idx: int
    price: float
    side: Side


def find_pivots(df: pd.DataFrame, left: int, right: int) -> list[Pivot]:
    """分形法识别枢轴高/低点。

    枢轴高: high 严格高于左右各 `left`/`right` 根的 high。
    """
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    n = len(df)
    pivots: list[Pivot] = []

    for i in range(left, n - right):
        window_h = highs[i - left : i + right + 1]
        if highs[i] == window_h.max() and (window_h == highs[i]).sum() == 1:
            pivots.append(Pivot(i, i + right, float(highs[i]), "resistance"))

        window_l = lows[i - left : i + right + 1]
        if lows[i] == window_l.min() and (window_l == lows[i]).sum() == 1:
            pivots.append(Pivot(i, i + right, float(lows[i]), "support"))

    return sorted(pivots, key=lambda p: p.confirm_idx)


# --------------------------------------------------------------------------
# 位置聚类(在线增量,天然因果)
# --------------------------------------------------------------------------


@dataclass
class Level:
    """一个支撑或压力位,随着新枢轴到来而增长。"""

    side: Side
    pivots: list[Pivot] = field(default_factory=list)

    @property
    def price(self) -> float:
        """当前位置价格 = 已归入的枢轴均价(只用已发生的)。"""
        return float(np.mean([p.price for p in self.pivots]))

    @property
    def touches(self) -> int:
        return len(self.pivots)

    def matches(self, pivot: Pivot, tol: float) -> bool:
        return (
            pivot.side == self.side
            and abs(pivot.price - self.price) / self.price <= tol
        )


@dataclass(frozen=True, slots=True)
class TouchEvent:
    """第 N 次触碰事件。因果快照: 只记录 confirm_idx 时刻已知的信息。"""

    symbol: str
    side: Side
    n_touch: int
    confirm_idx: int
    level_price: float
    span_bars: int          # 首次触碰到本次的跨度
    above_ma: bool          # 宏观代理: 价格是否在200日均线上方


def build_touch_events(
    df: pd.DataFrame,
    pivots: list[Pivot],
    symbol: str,
    ma: pd.Series,
    tol: float,
) -> list[TouchEvent]:
    """按时间顺序增量聚类,每次归入产生一个 TouchEvent。"""
    levels: list[Level] = []
    events: list[TouchEvent] = []
    closes = df["close"].to_numpy()
    ma_arr = ma.to_numpy()

    for piv in pivots:
        matched = next((lv for lv in levels if lv.matches(piv, tol)), None)

        if matched is None:
            matched = Level(side=piv.side)
            levels.append(matched)

        matched.pivots.append(piv)

        if matched.touches < MIN_TOUCHES:
            continue

        ci = piv.confirm_idx
        if ci >= len(df):
            continue

        ma_val = ma_arr[ci]
        events.append(
            TouchEvent(
                symbol=symbol,
                side=piv.side,
                n_touch=matched.touches,
                confirm_idx=ci,
                level_price=matched.price,
                span_bars=ci - matched.pivots[0].confirm_idx,
                above_ma=bool(closes[ci] > ma_val) if not np.isnan(ma_val) else False,
            )
        )

    return events


# --------------------------------------------------------------------------
# Q2: hazard 统计
# --------------------------------------------------------------------------


def measure_breakout(
    df: pd.DataFrame,
    event: TouchEvent,
    lookforward: int,
    threshold: float,
) -> bool | None:
    """确认时刻之后 `lookforward` 根内是否发生收盘突破。

    返回 None 表示数据不足(右侧删失),这类样本必须排除而不是当作未突破。
    """
    start = event.confirm_idx + 1
    end = start + lookforward

    if end > len(df):
        return None

    closes = df["close"].to_numpy()[start:end]
    lv = event.level_price

    if event.side == "resistance":
        return bool((closes > lv * (1 + threshold)).any())
    return bool((closes < lv * (1 - threshold)).any())


def hazard_table(
    df: pd.DataFrame,
    events: list[TouchEvent],
    lookforward: int,
    threshold: float,
    group_by_ma: bool = False,
) -> pd.DataFrame:
    """输出 触碰次数N -> 突破概率 的统计表。"""
    rows = []
    for ev in events:
        broke = measure_breakout(df, ev, lookforward, threshold)
        if broke is None:
            continue
        rows.append(
            {
                "symbol": ev.symbol,
                "side": ev.side,
                "n_touch": min(ev.n_touch, 6),  # N>=6 合并,样本太稀
                "above_ma": ev.above_ma,
                "broke": broke,
            }
        )

    if not rows:
        return pd.DataFrame()

    raw = pd.DataFrame(rows)
    keys = ["n_touch"] + (["above_ma"] if group_by_ma else [])

    return (
        raw.groupby(keys)
        .agg(样本数=("broke", "size"), 突破概率=("broke", "mean"))
        .assign(突破概率=lambda d: (d["突破概率"] * 100).round(1))
        .reset_index()
    )


# --------------------------------------------------------------------------
# Q1: 通道斜率诊断
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ChannelFit:
    slope: float
    norm_slope: float   # 无量纲: 斜率 × 跨度 / 区间高度
    r2: float
    n_points: int


def fit_channel(
    pivots: list[Pivot], side: Side, band_height: float
) -> ChannelFit | None:
    """对同侧枢轴做线性回归,判断该侧边界是水平还是倾斜。"""
    pts = [p for p in pivots if p.side == side]
    if len(pts) < 3 or band_height <= 0:
        return None

    x = np.array([p.idx for p in pts], dtype=float)
    y = np.array([p.price for p in pts], dtype=float)

    slope, intercept = np.polyfit(x, y, 1)
    pred = slope * x + intercept
    ss_res = float(((y - pred) ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0

    span = x.max() - x.min()
    return ChannelFit(
        slope=float(slope),
        norm_slope=float(slope * span / band_height),
        r2=float(r2),
        n_points=len(pts),
    )


def diagnose_channels(
    df: pd.DataFrame, pivots: list[Pivot], window: int, step: int
) -> pd.DataFrame:
    """滚动窗口做通道诊断,汇总归一化斜率分布。"""
    rows = []
    n = len(df)

    for start in range(0, n - window, step):
        end = start + window
        seg = [p for p in pivots if start <= p.idx < end]
        if len(seg) < 6:
            continue

        seg_df = df.iloc[start:end]
        height = float(seg_df["high"].max() - seg_df["low"].min())

        for side in ("resistance", "support"):
            fit = fit_channel(seg, side, height)  # type: ignore[arg-type]
            if fit is None:
                continue
            rows.append(
                {
                    "start_idx": start,
                    "side": side,
                    "norm_slope": fit.norm_slope,
                    "r2": fit.r2,
                    "n_points": fit.n_points,
                }
            )

    return pd.DataFrame(rows)


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------


def analyze(symbols: list[str], tf: str, db_path: Path) -> None:
    tf_hours = {"4h": 4, "1d": 24, "1h": 1}[tf]
    ma_bars = MA_PERIOD_DAYS * 24 // tf_hours

    all_events: list[TouchEvent] = []
    all_channels: list[pd.DataFrame] = []
    frames: dict[str, pd.DataFrame] = {}

    for symbol in symbols:
        raw = load_klines(db_path, symbol, "1h")
        df = resample_ohlcv(raw, tf) if tf != "1h" else raw
        frames[symbol] = df

        pivots = find_pivots(df, PIVOT_LEFT, PIVOT_RIGHT)
        ma = df["close"].rolling(ma_bars, min_periods=ma_bars).mean()

        events = build_touch_events(df, pivots, symbol, ma, LEVEL_TOLERANCE)
        all_events.extend(events)

        ch = diagnose_channels(df, pivots, window=250, step=50)
        ch["symbol"] = symbol
        all_channels.append(ch)

        logger.info(
            f"{symbol} {tf}: {len(df)} 根 | 枢轴 {len(pivots)} 个 | "
            f"触碰事件 {len(events)} 次"
        )

    # ---- Q1 通道诊断 ----
    print("\n" + "=" * 66)
    print("Q1  通道斜率诊断 —— 水平位模型还能用吗?")
    print("=" * 66)

    ch_all = pd.concat(all_channels, ignore_index=True)
    if ch_all.empty:
        print("样本不足,无法诊断")
    else:
        abs_slope = ch_all["norm_slope"].abs()
        print(f"\n滚动窗口数: {len(ch_all)}   (窗口 250 根, 步长 50 根)")
        print(f"归一化斜率绝对值  中位数 {abs_slope.median():.3f}"
              f"   75分位 {abs_slope.quantile(0.75):.3f}"
              f"   90分位 {abs_slope.quantile(0.90):.3f}")
        print(f"回归 R²           中位数 {ch_all['r2'].median():.3f}")
        print(f"\n  |斜率| < 0.3 占比: {(abs_slope < 0.3).mean() * 100:.1f}%  → 水平近似可用")
        print(f"  |斜率| > 0.5 占比: {(abs_slope > 0.5).mean() * 100:.1f}%  → 明显倾斜")
        print(f"  R² < 0.3    占比: {(ch_all['r2'] < 0.3).mean() * 100:.1f}%  → 无结构(随机)")

    # ---- Q2 hazard 曲线 ----
    print("\n" + "=" * 66)
    print("Q2  hazard 曲线 —— 触碰 N 次后的突破概率")
    print("=" * 66)

    for lf in LOOKFORWARD_BARS:
        merged = []
        for symbol in symbols:
            evs = [e for e in all_events if e.symbol == symbol]
            t = hazard_table(frames[symbol], evs, lf, BREAKOUT_THRESHOLD)
            if not t.empty:
                merged.append(t)

        if not merged:
            continue

        combined = (
            pd.concat(merged)
            .groupby("n_touch")
            .apply(
                lambda g: pd.Series(
                    {
                        "样本数": int(g["样本数"].sum()),
                        "突破概率": round(
                            float((g["突破概率"] * g["样本数"]).sum() / g["样本数"].sum()), 1
                        ),
                    }
                ),
                include_groups=False,
            )
            .reset_index()
        )

        print(f"\n--- 前瞻 {lf} 根 ({lf * tf_hours} 小时) ---")
        print(combined.to_string(index=False))

    # ---- 宏观代理分组 ----
    print("\n" + "=" * 66)
    print("Q2b  按 200 日均线上下分组 (前瞻 20 根)")
    print("=" * 66)

    merged_ma = []
    for symbol in symbols:
        evs = [e for e in all_events if e.symbol == symbol]
        t = hazard_table(frames[symbol], evs, 20, BREAKOUT_THRESHOLD, group_by_ma=True)
        if not t.empty:
            merged_ma.append(t)

    if merged_ma:
        cm = (
            pd.concat(merged_ma)
            .groupby(["above_ma", "n_touch"])
            .apply(
                lambda g: pd.Series(
                    {
                        "样本数": int(g["样本数"].sum()),
                        "突破概率": round(
                            float((g["突破概率"] * g["样本数"]).sum() / g["样本数"].sum()), 1
                        ),
                    }
                ),
                include_groups=False,
            )
            .reset_index()
        )
        print(cm.to_string(index=False))

    print("\n参数快照: "
          f"pivot={PIVOT_LEFT}/{PIVOT_RIGHT}  容差={LEVEL_TOLERANCE:.1%}  "
          f"突破阈值={BREAKOUT_THRESHOLD:.1%}\n")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="支撑压力位诊断 + hazard 曲线")
    p.add_argument("--symbols", nargs="+",
                   default=["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"])
    p.add_argument("--tf", default="4h", choices=["1h", "4h", "1d"])
    p.add_argument("--db", type=Path, default=DB_PATH)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    analyze(args.symbols, args.tf, args.db)


if __name__ == "__main__":
    main()