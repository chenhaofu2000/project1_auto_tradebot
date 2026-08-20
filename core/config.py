"""
Configuration loader.

Loads config.yaml from the project root, then overlays any secrets
provided via environment variables.

Secret resolution order for the LLM API key (highest priority first):
    1. Environment variable LLM_API_KEY
    2. config.yaml -> llm.api_key

This lets the same code run three ways with no changes:
- Local dev:     key sits in config.yaml (convenient)
- Docker:        key injected as an env var at run time (not baked in)
- AWS EC2:       env var populated from SSM/Secrets Manager at startup

The point is that once the key lives in the environment, there is no
plaintext key on disk at all -- which is exactly the failure mode that
kept biting this project.
"""

import os
from pathlib import Path
from typing import Any

import yaml

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"

# Placeholder value shipped in the template; treated as "no key set".
_PLACEHOLDER = "PUT_YOUR_BAILIAN_KEY_HERE"


def load_config() -> dict[str, Any]:
    """Load config.yaml and overlay secrets from the environment."""
    if not _CONFIG_PATH.exists():
        raise FileNotFoundError(
            f"config.yaml not found at {_CONFIG_PATH}. Copy the template and fill in your settings."
        )
    with open(_CONFIG_PATH, encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    _overlay_secrets(config)
    return config


def _overlay_secrets(config: dict[str, Any]) -> None:
    """Mutate `config` in place, replacing secret fields with values
    from environment variables when present."""
    llm = config.setdefault("llm", {})

    env_key = os.environ.get("LLM_API_KEY", "").strip()
    file_key = (llm.get("api_key") or "").strip()

    # Environment wins. Fall back to the file value only if the env var
    # is unset. The placeholder never counts as a real key.
    resolved = env_key or file_key
    if resolved == _PLACEHOLDER:
        resolved = ""

    llm["api_key"] = resolved

    if not resolved:
        # Fail early with a clear message instead of letting the LLM
        # client fail later with an opaque auth error.
        raise ValueError(
            "No LLM API key found. Set the LLM_API_KEY environment "
            "variable, or put it in config.yaml under llm.api_key."
        )
