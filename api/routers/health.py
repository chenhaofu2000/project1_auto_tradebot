"""
Health and status endpoints.

Used by the dashboard to show whether the collector is alive and how
fresh the data is.
"""

from fastapi import APIRouter, HTTPException

from api import db

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
def health() -> dict:
    """Row counts and last-update timestamps for every table.

    A stale `last_update` is the clearest signal that the collector has
    stopped, so the dashboard can surface it without needing a live
    connection to the collector process.
    """
    try:
        return db.get_health()
    except db.DatabaseNotReady as e:
        raise HTTPException(status_code=503, detail=str(e))
