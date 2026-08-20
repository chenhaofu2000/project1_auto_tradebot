"""
Config file read/write for the API layer.

SECRET HANDLING (deliberate, not incidental):

Two independent guarantees, both enforced structurally rather than by
convention:

1. Reads never expose the key. `load_public_config()` replaces
   llm.api_key with a masked placeholder before the value ever leaves
   this module, so no endpoint can leak it even by accident.

2. Writes never destroy the key. `apply_updates()` starts from the
   on-disk config and deep-merges only the incoming changes. A field
   that is absent from the request keeps its existing value, and the
   masked placeholder is explicitly rejected so a client that reads the
   config and writes it straight back cannot overwrite a real key with
   "sk-***".

This exists because a config file that gets clobbered during an update
silently destroys credentials -- a mistake that is easy to make once
and expensive to discover later.
"""

import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"
BACKUP_DIR = Path(__file__).resolve().parent.parent / "data" / "config_backups"

MASK = "********"

# Only these paths may be modified through the API. Anything else in the
# request body is ignored. Keeping this explicit means a malformed or
# malicious payload cannot inject arbitrary config keys.
EDITABLE_FIELDS: set[tuple[str, ...]] = {
    ("llm", "base_url"),
    ("llm", "api_key"),
    ("llm", "model"),
    ("news_crawler", "poll_interval"),
    ("news_crawler", "request_timeout"),
    ("sentiment", "core_coins"),
    ("sentiment", "batch_window"),
    ("sentiment", "batch_max_size"),
    ("sentiment", "window_minutes"),
    ("sentiment", "hot_mention_threshold"),
    ("storage", "flush_interval"),
    ("technical", "symbols"),
    ("technical", "interval"),
    ("technical", "lookback"),
    ("technical", "poll_interval"),
    ("technical", "request_timeout"),
    ("technical", "rsi_period"),
    ("technical", "momentum_lookback"),
    ("technical", "momentum_clip_pct"),
    ("technical", "volume_avg_periods"),
    ("technical", "ema_fast"),
    ("technical", "ema_slow"),
}


class ConfigError(Exception):
    pass


def _load_raw() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        raise ConfigError(f"config.yaml not found at {CONFIG_PATH}")
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_public_config() -> dict[str, Any]:
    """Config safe to send to a client: the API key is replaced with a
    mask, and a boolean records whether a real key is configured."""
    cfg = _load_raw()
    llm = dict(cfg.get("llm", {}))
    key = llm.get("api_key") or ""
    configured = bool(key) and key != "PUT_YOUR_BAILIAN_KEY_HERE"
    llm["api_key"] = MASK if configured else ""
    llm["api_key_configured"] = configured
    cfg["llm"] = llm
    return cfg


def _backup() -> Path:
    """Timestamped copy of the current config, kept before every write.
    Cheap insurance: the file is tiny and a bad edit is otherwise
    unrecoverable."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    dest = BACKUP_DIR / f"config.{stamp}.yaml"
    shutil.copy2(CONFIG_PATH, dest)
    return dest


def _validate(path: tuple[str, ...], value: Any) -> Any:
    """Type/range checks for the fields where a bad value would break
    the collector in a way that is annoying to debug."""
    section, field = path

    positive_numbers = {
        ("news_crawler", "poll_interval"),
        ("news_crawler", "request_timeout"),
        ("sentiment", "batch_window"),
        ("storage", "flush_interval"),
        ("technical", "poll_interval"),
        ("technical", "request_timeout"),
        ("technical", "momentum_clip_pct"),
    }
    positive_ints = {
        ("sentiment", "batch_max_size"),
        ("sentiment", "window_minutes"),
        ("sentiment", "hot_mention_threshold"),
        ("technical", "lookback"),
        ("technical", "rsi_period"),
        ("technical", "momentum_lookback"),
        ("technical", "volume_avg_periods"),
        ("technical", "ema_fast"),
        ("technical", "ema_slow"),
    }

    if path in positive_numbers:
        v = float(value)
        if v <= 0:
            raise ConfigError(f"{section}.{field} must be > 0, got {v}")
        return v

    if path in positive_ints:
        v = int(value)
        if v <= 0:
            raise ConfigError(f"{section}.{field} must be > 0, got {v}")
        return v

    if path in {("sentiment", "core_coins"), ("technical", "symbols")}:
        if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
            raise ConfigError(f"{section}.{field} must be a list of strings")
        return [str(x).strip().upper() for x in value if str(x).strip()]

    return value


def apply_updates(updates: dict[str, Any]) -> dict[str, Any]:
    """Merge `updates` into config.yaml and write it back.

    Returns the resulting public (masked) config. Raises ConfigError on
    any validation failure -- and validates everything BEFORE touching
    the file, so a rejected request leaves the config untouched rather
    than half-applied.
    """
    current = _load_raw()

    # Phase 1: collect and validate. Nothing is written during this pass.
    staged: list[tuple[tuple[str, ...], Any]] = []
    ignored: list[str] = []

    for section, fields in updates.items():
        if not isinstance(fields, dict):
            ignored.append(section)
            continue
        for field, value in fields.items():
            path = (section, field)
            if path not in EDITABLE_FIELDS:
                ignored.append(f"{section}.{field}")
                continue
            # Never let the mask overwrite a real stored key.
            if path == ("llm", "api_key") and (value == MASK or value == ""):
                continue
            staged.append((path, _validate(path, value)))

    if not staged:
        return load_public_config()

    # Phase 2: apply. Validation already passed, so this cannot fail
    # partway through and leave an inconsistent file.
    backup = _backup()
    for (section, field), value in staged:
        current.setdefault(section, {})[field] = value

    tmp = CONFIG_PATH.with_suffix(".yaml.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        yaml.safe_dump(current, f, allow_unicode=True, sort_keys=False)
    tmp.replace(CONFIG_PATH)  # atomic swap: no truncated file if we crash

    result = load_public_config()
    result["_meta"] = {
        "updated_fields": [f"{s}.{f}" for (s, f), _ in staged],
        "ignored_fields": ignored,
        "backup": str(backup),
    }
    return result
