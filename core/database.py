"""
SQLite database setup.

Single-file database at data/tradebot.db. Uses aiosqlite for
non-blocking writes from async plugins.

Schema:
- news:      raw articles from the crawler
- sentiment: per-item LLM scoring results
- snapshots: periodic rolling aggregator state (for time-series charts)
- technical: raw technical indicators computed from K-lines
- klines:    historical OHLCV backfill (written by scripts/download_history.py)
"""

from pathlib import Path

import aiosqlite

_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "tradebot.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS news (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id TEXT,
    feed TEXT,
    title TEXT,
    link TEXT,
    published TEXT,
    received_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sentiment (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    feed TEXT,
    link TEXT,
    coins TEXT,        -- JSON array as text, e.g. '["BTC", "ETH"]'
    score REAL,
    reason TEXT,
    scored_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    coin TEXT NOT NULL,
    avg_score REAL,
    mentions INTEGER,
    is_hot INTEGER,     -- 0 or 1
    captured_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS technical (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    momentum_score REAL,
    rsi REAL,
    volume_ratio REAL,
    price_vs_ema45 REAL,
    price_vs_ema125 REAL,
    ema45_vs_ema125 REAL,
    ema45 REAL,
    ema125 REAL,
    close_price REAL,
    computed_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sentiment_scored_at ON sentiment(scored_at);
CREATE INDEX IF NOT EXISTS idx_snapshots_coin_time ON snapshots(coin, captured_at);
CREATE INDEX IF NOT EXISTS idx_technical_symbol_time ON technical(symbol, computed_at);
"""


async def init_db() -> None:
    """Create the data directory and tables if they don't exist yet."""
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(_DB_PATH) as db:
        await db.executescript(_SCHEMA)
        await db.commit()


def get_db_path() -> Path:
    return _DB_PATH
