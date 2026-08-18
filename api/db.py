"""
Read-only database access for the API layer.

Design note: this uses the synchronous stdlib `sqlite3` rather than
aiosqlite, even though the collector plugins use aiosqlite. Reasons:

1. These are short read-only queries. FastAPI automatically runs plain
   `def` endpoints in a threadpool, so they never block the event loop.
2. A fresh connection per request keeps the API stateless -- it can be
   started, stopped and restarted independently of the collector, and
   there is no shared connection to leak or invalidate.
3. Opening in read-only URI mode makes it structurally impossible for
   the dashboard to corrupt collector data.

The API is a separate process from main.py: the collector writes,
the API only reads.
"""

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "tradebot.db"


class DatabaseNotReady(Exception):
    """Raised when the database file does not exist yet, i.e. the
    collector has never been run."""


def _connect() -> sqlite3.Connection:
    """Open a read-only connection. Fails loudly if the DB is missing
    rather than silently creating an empty one."""
    if not DB_PATH.exists():
        raise DatabaseNotReady(
            f"Database not found at {DB_PATH}. Run main.py at least once."
        )
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _cutoff_iso(hours: float) -> str:
    """UTC cutoff timestamp in the same ISO format the collector writes.
    ISO-8601 UTC strings sort lexicographically, so plain string
    comparison in SQL gives correct chronological filtering."""
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


# ----------------------------------------------------------------------
# Health / status
# ----------------------------------------------------------------------
def get_health() -> dict[str, Any]:
    """Row counts and last-updated timestamps for each table."""
    with _connect() as conn:
        tables = {
            "news": "received_at",
            "sentiment": "scored_at",
            "technical": "computed_at",
            "snapshots": "captured_at",
        }
        stats: dict[str, Any] = {}
        for table, time_col in tables.items():
            try:
                row = conn.execute(
                    f"SELECT COUNT(*) AS n, MAX({time_col}) AS last FROM {table}"
                ).fetchone()
                stats[table] = {"rows": row["n"], "last_update": row["last"]}
            except sqlite3.OperationalError:
                # Table not created yet (older DB file).
                stats[table] = {"rows": 0, "last_update": None}

        try:
            row = conn.execute(
                "SELECT COUNT(*) AS n, MIN(open_time) AS lo, MAX(open_time) AS hi "
                "FROM klines"
            ).fetchone()
            stats["klines"] = {
                "rows": row["n"],
                "first_open_time": row["lo"],
                "last_open_time": row["hi"],
            }
        except sqlite3.OperationalError:
            stats["klines"] = {"rows": 0}

    return {"database": str(DB_PATH), "tables": stats}


# ----------------------------------------------------------------------
# Sentiment
# ----------------------------------------------------------------------
def _explode_coins(rows: list[sqlite3.Row]) -> list[tuple[str, float, str]]:
    """Expand rows whose `coins` column holds a JSON array into one
    (coin, score, timestamp) tuple per coin mentioned."""
    out: list[tuple[str, float, str]] = []
    for r in rows:
        try:
            coins = json.loads(r["coins"] or "[]")
        except json.JSONDecodeError:
            continue
        for coin in coins:
            if coin:
                out.append((coin, r["score"] or 0.0, r["scored_at"]))
    return out


def get_sentiment_by_coin(window_hours: float = 1.0) -> list[dict[str, Any]]:
    """Rolling per-coin sentiment over the given window.

    This recomputes what the live aggregator holds in memory. Doing it
    from stored rows means the dashboard works even when the collector
    is not running.
    """
    with _connect() as conn:
        rows = conn.execute(
            "SELECT coins, score, scored_at FROM sentiment WHERE scored_at >= ?",
            (_cutoff_iso(window_hours),),
        ).fetchall()

    buckets: dict[str, list[float]] = {}
    for coin, score, _ in _explode_coins(rows):
        buckets.setdefault(coin, []).append(score)

    result = [
        {
            "coin": coin,
            "avg_score": sum(scores) / len(scores),
            "mentions": len(scores),
        }
        for coin, scores in buckets.items()
    ]
    result.sort(key=lambda d: d["mentions"], reverse=True)
    return result


def get_sentiment_history(coin: str, hours: float = 24.0) -> list[dict[str, Any]]:
    """Individual scored data points for one coin, oldest first."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT coins, score, scored_at, title FROM sentiment "
            "WHERE scored_at >= ? ORDER BY scored_at ASC",
            (_cutoff_iso(hours),),
        ).fetchall()

    target = coin.upper()
    out: list[dict[str, Any]] = []
    for r in rows:
        try:
            coins = json.loads(r["coins"] or "[]")
        except json.JSONDecodeError:
            continue
        if target in [c.upper() for c in coins]:
            out.append(
                {
                    "timestamp": r["scored_at"],
                    "score": r["score"],
                    "title": r["title"],
                }
            )
    return out


def get_recent_news(limit: int = 50) -> list[dict[str, Any]]:
    """Most recently scored news items, newest first."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT title, feed, link, coins, score, reason, scored_at "
            "FROM sentiment ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()

    out = []
    for r in rows:
        try:
            coins = json.loads(r["coins"] or "[]")
        except json.JSONDecodeError:
            coins = []
        out.append(
            {
                "title": r["title"],
                "feed": r["feed"],
                "link": r["link"],
                "coins": coins,
                "score": r["score"],
                "reason": r["reason"],
                "scored_at": r["scored_at"],
            }
        )
    return out


# ----------------------------------------------------------------------
# Technical indicators
# ----------------------------------------------------------------------
_TECH_COLUMNS = (
    "symbol, momentum_score, rsi, volume_ratio, price_vs_ema45, "
    "price_vs_ema125, ema45_vs_ema125, ema45, ema125, close_price, computed_at"
)


def get_technical_latest() -> list[dict[str, Any]]:
    """Most recent indicator row per symbol.

    Uses a correlated subquery on MAX(computed_at) rather than
    GROUP BY, so every returned column belongs to the same row.
    """
    with _connect() as conn:
        rows = conn.execute(
            f"""SELECT {_TECH_COLUMNS} FROM technical t
                WHERE computed_at = (
                    SELECT MAX(computed_at) FROM technical
                    WHERE symbol = t.symbol
                )
                GROUP BY symbol
                ORDER BY symbol"""
        ).fetchall()
    return [dict(r) for r in rows]


def get_technical_history(symbol: str, hours: float = 168.0) -> list[dict[str, Any]]:
    """Indicator time series for one symbol, oldest first.

    Consecutive duplicate rows are collapsed: when the poll interval is
    shorter than the candle interval, the same closed candle is
    recomputed repeatedly and would otherwise flood the chart with
    identical points.
    """
    with _connect() as conn:
        rows = conn.execute(
            f"""SELECT {_TECH_COLUMNS} FROM technical
                WHERE symbol = ? AND computed_at >= ?
                ORDER BY computed_at ASC""",
            (symbol.upper(), _cutoff_iso(hours)),
        ).fetchall()

    out: list[dict[str, Any]] = []
    prev_signature = None
    for r in rows:
        d = dict(r)
        signature = (d["close_price"], d["rsi"], d["volume_ratio"])
        if signature != prev_signature:
            out.append(d)
            prev_signature = signature
    return out


def get_technical_symbols() -> list[str]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT symbol FROM technical ORDER BY symbol"
        ).fetchall()
    return [r["symbol"] for r in rows]


# ----------------------------------------------------------------------
# K-lines
# ----------------------------------------------------------------------
def get_klines(
    symbol: str, interval: str = "1h", limit: int = 200
) -> list[dict[str, Any]]:
    """Historical candles, oldest first (chart libraries expect
    ascending time). Fetches the newest `limit` rows, then reverses."""
    with _connect() as conn:
        rows = conn.execute(
            """SELECT open_time, open, high, low, close, volume FROM klines
               WHERE symbol = ? AND interval = ?
               ORDER BY open_time DESC LIMIT ?""",
            (symbol.upper(), interval, limit),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


def get_kline_symbols() -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            """SELECT symbol, interval, COUNT(*) AS candles
               FROM klines GROUP BY symbol, interval ORDER BY symbol"""
        ).fetchall()
    return [dict(r) for r in rows]
