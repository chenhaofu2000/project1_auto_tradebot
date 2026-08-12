"""
Download historical K-lines from Binance into SQLite.

Binance caps each request at 1000 candles, so this pages backwards
through time using the `startTime` parameter until it reaches the
requested start date.

This is a ONE-OFF backfill script, not a plugin -- historical price
data can always be re-downloaded on demand, unlike sentiment data
which must be accumulated live.

Usage:
    uv run python scripts/download_history.py
    uv run python scripts/download_history.py --symbols BTCUSDT --interval 1d --days 1095
"""

import argparse
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "tradebot.db"
BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS klines (
    symbol TEXT NOT NULL,
    interval TEXT NOT NULL,
    open_time INTEGER NOT NULL,
    open REAL, high REAL, low REAL, close REAL, volume REAL,
    PRIMARY KEY (symbol, interval, open_time)
);
CREATE INDEX IF NOT EXISTS idx_klines_lookup ON klines(symbol, interval, open_time);
"""


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)
    conn.commit()


def fetch_page(symbol: str, interval: str, start_ms: int) -> list[list]:
    """Fetch up to 1000 candles starting at start_ms."""
    resp = requests.get(
        BINANCE_KLINES_URL,
        params={
            "symbol": symbol,
            "interval": interval,
            "startTime": start_ms,
            "limit": 1000,
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def download(conn: sqlite3.Connection, symbol: str, interval: str, days: int) -> int:
    start = datetime.now(timezone.utc) - timedelta(days=days)
    start_ms = int(start.timestamp() * 1000)
    now_ms = int(time.time() * 1000)

    total = 0
    while start_ms < now_ms:
        page = fetch_page(symbol, interval, start_ms)
        if not page:
            break

        rows = [
            (
                symbol, interval, int(c[0]),
                float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5]),
            )
            for c in page
        ]
        # INSERT OR REPLACE makes re-running safe (idempotent backfill).
        conn.executemany(
            """INSERT OR REPLACE INTO klines
               (symbol, interval, open_time, open, high, low, close, volume)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        conn.commit()
        total += len(rows)

        last_open = int(page[-1][0])
        if last_open <= start_ms:
            break                       # no forward progress; avoid infinite loop
        start_ms = last_open + 1

        readable = datetime.fromtimestamp(last_open / 1000, timezone.utc)
        print(f"  {symbol} {interval}: {total} candles, up to {readable:%Y-%m-%d %H:%M}")

        time.sleep(0.25)                # stay well under Binance rate limits

    return total


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT")
    p.add_argument("--interval", default="1h")
    p.add_argument("--days", type=int, default=730)
    args = p.parse_args()

    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_DB_PATH)
    init_schema(conn)

    for symbol in args.symbols.split(","):
        symbol = symbol.strip()
        print(f"\nDownloading {symbol} {args.interval}, last {args.days} days...")
        n = download(conn, symbol, args.interval, args.days)
        print(f"  DONE: {n} candles")

    # Summary
    print("\n" + "=" * 60)
    cur = conn.execute(
        """SELECT symbol, interval, COUNT(*),
                  MIN(open_time), MAX(open_time)
           FROM klines GROUP BY symbol, interval"""
    )
    for sym, iv, cnt, lo, hi in cur:
        lo_d = datetime.fromtimestamp(lo / 1000, timezone.utc)
        hi_d = datetime.fromtimestamp(hi / 1000, timezone.utc)
        print(f"{sym:10s} {iv:4s} {cnt:6d} candles  {lo_d:%Y-%m-%d} -> {hi_d:%Y-%m-%d}")
    conn.close()


if __name__ == "__main__":
    main()