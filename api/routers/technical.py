"""
Technical indicator and K-line endpoints (Module B data).

Like the sentiment endpoints, these expose raw indicator values only.
Weighting them into a signal is a strategy concern kept out of the API.
"""

from fastapi import APIRouter, HTTPException, Query

from api import db

router = APIRouter(prefix="/api", tags=["technical"])


@router.get("/technical/latest")
def technical_latest() -> list[dict]:
    """Most recent indicator row per symbol."""
    try:
        return db.get_technical_latest()
    except db.DatabaseNotReady as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.get("/technical/history")
def technical_history(
    symbol: str = Query(..., min_length=1, description="e.g. BTCUSDT"),
    hours: float = Query(168.0, gt=0, le=8760),
) -> list[dict]:
    """Indicator time series for one symbol, oldest first.

    Consecutive identical rows are collapsed. When the poll interval is
    shorter than the candle interval the collector recomputes the same
    closed candle repeatedly, and those duplicates would otherwise
    dominate the series.
    """
    try:
        return db.get_technical_history(symbol, hours)
    except db.DatabaseNotReady as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.get("/technical/symbols")
def technical_symbols() -> list[str]:
    try:
        return db.get_technical_symbols()
    except db.DatabaseNotReady as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.get("/klines")
def klines(
    symbol: str = Query(..., min_length=1),
    interval: str = Query("1h"),
    limit: int = Query(200, ge=1, le=5000),
) -> list[dict]:
    """OHLCV candles, oldest first (charting libraries expect
    ascending time order)."""
    try:
        return db.get_klines(symbol, interval, limit)
    except db.DatabaseNotReady as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.get("/klines/available")
def klines_available() -> list[dict]:
    """Which symbol/interval combinations have been backfilled."""
    try:
        return db.get_kline_symbols()
    except db.DatabaseNotReady as e:
        raise HTTPException(status_code=503, detail=str(e))
