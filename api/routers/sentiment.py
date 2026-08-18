"""
Sentiment endpoints (Module A data).

All values are raw factor outputs. No buy/sell interpretation is
applied here -- that belongs in the strategy layer.
"""

from fastapi import APIRouter, HTTPException, Query

from api import db

router = APIRouter(prefix="/api/sentiment", tags=["sentiment"])


@router.get("/coins")
def sentiment_by_coin(
    window_hours: float = Query(1.0, gt=0, le=720, description="Rolling window"),
) -> list[dict]:
    """Average sentiment and mention count per coin over the window.

    Recomputed from stored rows rather than read from the live
    aggregator, so this works whether or not the collector is running.
    """
    try:
        return db.get_sentiment_by_coin(window_hours)
    except db.DatabaseNotReady as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.get("/history")
def sentiment_history(
    coin: str = Query(..., min_length=1, description="Ticker, e.g. BTC"),
    hours: float = Query(24.0, gt=0, le=8760),
) -> list[dict]:
    """Individual scored data points for one coin, oldest first."""
    try:
        return db.get_sentiment_history(coin, hours)
    except db.DatabaseNotReady as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.get("/news")
def recent_news(limit: int = Query(50, ge=1, le=500)) -> list[dict]:
    """Most recently scored headlines with their score and reason."""
    try:
        return db.get_recent_news(limit)
    except db.DatabaseNotReady as e:
        raise HTTPException(status_code=503, detail=str(e))
