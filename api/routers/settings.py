"""
Configuration endpoints.

Lets the dashboard change factor parameters without editing YAML by
hand. Secret handling is enforced in settings_store, not here -- see
that module for why.

Note: the collector reads config.yaml once at startup, so changes take
effect on its next restart. The response says so explicitly rather than
letting the user assume a silent hot-reload happened.
"""

from typing import Any

from fastapi import APIRouter, Body, HTTPException

from api import settings_store as store

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
def read_settings() -> dict:
    """Current configuration with the API key masked."""
    try:
        return store.load_public_config()
    except store.ConfigError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.put("")
def update_settings(updates: dict[str, Any] = Body(...)) -> dict:
    """Merge partial updates into config.yaml.

    Only whitelisted fields are applied; anything else is reported back
    in `_meta.ignored_fields` rather than silently dropped. Validation
    runs before any write, so a rejected request leaves the file
    untouched instead of half-applied.

    Omitting llm.api_key (or sending the mask) preserves the stored key.
    """
    try:
        result = store.apply_updates(updates)
    except store.ConfigError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=f"Invalid value: {e}")

    result.setdefault("_meta", {})["note"] = (
        "Saved. The collector loads config at startup, "
        "so restart main.py for changes to take effect."
    )
    return result
