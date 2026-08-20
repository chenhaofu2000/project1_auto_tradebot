"""历史 K 线补拉脚本。

从 Binance 公开接口分页拉取指定交易对的历史 K 线,写入本地 SQLite。
幂等:重复运行不会产生重复数据(依赖 UNIQUE 索引 + INSERT OR IGNORE)。

用法:
    uv run python -m scripts.fetch_history
    uv run python -m scripts.fetch_history --symbols BTCUSDT ETHUSDT --days 730
    uv run python -m scripts.fetch_history --interval 4h --days 365

注意:本脚本只读公开行情接口,无需 API key。
"""

from __future__ import annotations

import argparse
import asyncio
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Final, Sequence

import httpx
from loguru import logger

PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
DB_PATH: Final[Path] = PROJECT_ROOT / "data" / "market.db"

BINANCE_KLINES_URL: Final[str] = "https://api.binance.com/api/v3/klines"
MAX_LIMIT: Final[int] = 1000
REQUEST_INTERVAL_SEC: Final[float] = 0.25
MAX_RETRIES: Final[int] = 4

DEFAULT_SYMBOLS: Final[tuple[str, ...]] = (
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "BNBUSDT",
)

INTERVAL_TO_MS: Final[dict[str, int]] = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}


@dataclass(frozen=True, slots=True)
class Kline:
    """单根 K 线。时间戳单位为毫秒 (UTC)。"""

    symbol: str
    interval: str
    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    close_time: int
    quote_volume: float
    trade_count: int

    @classmethod
    def from_binance(cls, symbol: str, interval: str, raw: Sequence) -> "Kline":
        return cls(
            symbol=symbol,
            interval=interval,
            open_time=int(raw[0]),
            open=float(raw[1]),
            high=float(raw[2]),
            low=float(raw[3]),
            close=float(raw[4]),
            volume=float(raw[5]),
            close_time=int(raw[6]),
            quote_volume=float(raw[7]),
            trade_count=int(raw[8]),
        )


# --------------------------------------------------------------------------
# 数据库
# --------------------------------------------------------------------------

SCHEMA: Final[str] = """
CREATE TABLE IF NOT EXISTS klines_history (
    symbol        TEXT    NOT NULL,
    interval      TEXT    NOT NULL,
    open_time     INTEGER NOT NULL,
    open          REAL    NOT NULL,
    high          REAL    NOT NULL,
    low           REAL    NOT NULL,
    close         REAL    NOT NULL,
    volume        REAL    NOT NULL,
    close_time    INTEGER NOT NULL,
    quote_volume  REAL    NOT NULL,
    trade_count   INTEGER NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_klines_history_unique
    ON klines_history (symbol, interval, open_time);

CREATE INDEX IF NOT EXISTS idx_klines_history_lookup
    ON klines_history (symbol, interval, open_time DESC);
"""


def init_db(db_path: Path) -> None:
    """建表 + 建索引。已存在则跳过。"""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(SCHEMA)
    logger.info(f"数据库就绪: {db_path}")


def write_klines(db_path: Path, klines: list[Kline]) -> int:
    """批量写入。返回实际新增行数(重复的会被 IGNORE 掉)。"""
    if not klines:
        return 0

    rows = [
        (
            k.symbol, k.interval, k.open_time, k.open, k.high, k.low,
            k.close, k.volume, k.close_time, k.quote_volume, k.trade_count,
        )
        for k in klines
    ]

    with sqlite3.connect(db_path) as conn:
        before = conn.total_changes
        conn.executemany(
            """
            INSERT OR IGNORE INTO klines_history
                (symbol, interval, open_time, open, high, low,
                 close, volume, close_time, quote_volume, trade_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        inserted = conn.total_changes - before

    return inserted


def get_existing_range(
    db_path: Path, symbol: str, interval: str
) -> tuple[int | None, int | None]:
    """返回已有数据的 (最早 open_time, 最晚 open_time),空表返回 (None, None)。"""
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT MIN(open_time), MAX(open_time)
            FROM klines_history
            WHERE symbol = ? AND interval = ?
            """,
            (symbol, interval),
        ).fetchone()
    return (row[0], row[1]) if row else (None, None)


# --------------------------------------------------------------------------
# 拉取
# --------------------------------------------------------------------------


async def fetch_page(
    client: httpx.AsyncClient,
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
) -> list[list]:
    """拉取单页(最多 1000 根)。带指数退避重试。"""
    params = {
        "symbol": symbol,
        "interval": interval,
        "startTime": start_ms,
        "endTime": end_ms,
        "limit": MAX_LIMIT,
    }

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = await client.get(BINANCE_KLINES_URL, params=params, timeout=20.0)

            # 429/418 = 限流,必须退避
            if resp.status_code in (429, 418):
                wait = 2 ** attempt
                logger.warning(f"{symbol} 被限流 ({resp.status_code}),等待 {wait}s")
                await asyncio.sleep(wait)
                continue

            resp.raise_for_status()
            return resp.json()

        except (httpx.HTTPError, ValueError) as exc:
            if attempt == MAX_RETRIES:
                logger.error(f"{symbol} 第 {attempt} 次仍失败: {exc}")
                raise
            wait = 2 ** attempt
            logger.warning(f"{symbol} 第 {attempt} 次失败 ({exc}),{wait}s 后重试")
            await asyncio.sleep(wait)

    return []


async def fetch_symbol(
    client: httpx.AsyncClient,
    db_path: Path,
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
) -> int:
    """拉完单个交易对的整个时间区间。返回新增根数。"""
    step_ms = INTERVAL_TO_MS[interval]
    cursor = start_ms
    total_new = 0
    page = 0

    logger.info(
        f"开始拉取 {symbol} {interval} | "
        f"{_fmt(start_ms)} → {_fmt(end_ms)}"
    )

    while cursor < end_ms:
        page += 1
        raw = await fetch_page(client, symbol, interval, cursor, end_ms)

        if not raw:
            logger.info(f"{symbol} 第 {page} 页无数据,结束")
            break

        klines = [Kline.from_binance(symbol, interval, r) for r in raw]

        # 丢弃未收盘的最后一根,防未来函数
        now_ms = int(time.time() * 1000)
        klines = [k for k in klines if k.close_time < now_ms]

        if not klines:
            break

        new = write_klines(db_path, klines)
        total_new += new

        last_open = klines[-1].open_time
        logger.debug(
            f"{symbol} 第 {page} 页: 收到 {len(klines)} 根, "
            f"新增 {new} 根, 游标 {_fmt(last_open)}"
        )

        # 游标推进到最后一根之后,避免死循环
        next_cursor = last_open + step_ms
        if next_cursor <= cursor:
            logger.warning(f"{symbol} 游标未推进,强制结束")
            break
        cursor = next_cursor

        await asyncio.sleep(REQUEST_INTERVAL_SEC)

    logger.success(f"{symbol} {interval} 完成: 共 {page} 页, 新增 {total_new} 根")
    return total_new


def _fmt(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------


async def run(
    symbols: Sequence[str],
    interval: str,
    days: int,
    db_path: Path,
) -> None:
    if interval not in INTERVAL_TO_MS:
        raise ValueError(f"不支持的周期: {interval},可选 {list(INTERVAL_TO_MS)}")

    init_db(db_path)

    end = datetime.now(tz=timezone.utc)
    start = end - timedelta(days=days)
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)

    expected = (end_ms - start_ms) // INTERVAL_TO_MS[interval]
    logger.info(
        f"计划拉取 {len(symbols)} 个交易对 × 约 {expected} 根 "
        f"({interval}, 近 {days} 天)"
    )

    # 串行拉取,避免并发触发 Binance 权重限流
    async with httpx.AsyncClient() as client:
        grand_total = 0
        for symbol in symbols:
            try:
                grand_total += await fetch_symbol(
                    client, db_path, symbol, interval, start_ms, end_ms
                )
            except Exception as exc:
                logger.error(f"{symbol} 拉取中断: {exc}")
                continue

    logger.success(f"全部完成,累计新增 {grand_total} 根 K 线")

    # 收尾核对
    for symbol in symbols:
        lo, hi = get_existing_range(db_path, symbol, interval)
        if lo is None:
            logger.warning(f"{symbol} 库中无数据")
        else:
            with sqlite3.connect(db_path) as conn:
                count = conn.execute(
                    "SELECT COUNT(*) FROM klines_history WHERE symbol=? AND interval=?",
                    (symbol, interval),
                ).fetchone()[0]
            logger.info(
                f"{symbol} {interval}: {count} 根 | {_fmt(lo)} → {_fmt(hi)}"
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="补拉 Binance 历史 K 线")
    parser.add_argument(
        "--symbols", nargs="+", default=list(DEFAULT_SYMBOLS),
        help="交易对列表,默认 BTC/ETH/SOL/BNB",
    )
    parser.add_argument(
        "--interval", default="1h",
        help=f"K 线周期,可选 {list(INTERVAL_TO_MS)},默认 1h",
    )
    parser.add_argument(
        "--days", type=int, default=730,
        help="往前拉取的天数,默认 730(两年)",
    )
    parser.add_argument(
        "--db", type=Path, default=DB_PATH,
        help="SQLite 路径,默认 data/market.db",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    asyncio.run(run(args.symbols, args.interval, args.days, args.db))


if __name__ == "__main__":
    main()