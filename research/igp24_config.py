"""Shared runtime configuration for the IGP24 tools.

Secrets are loaded at request time rather than import time so read-only tools,
tests, and credential-free workers can import the code safely.
"""

from __future__ import annotations

import os
from pathlib import Path


DEFAULT_API_BASE = "https://api.sair.foundation/api/public/v1/competitions/igp24"
API_BASE = os.environ.get("IGP24_API_BASE", DEFAULT_API_BASE).rstrip("/")


class ConfigurationError(RuntimeError):
    """Raised when required runtime configuration is unavailable."""


def api_key() -> str:
    """Resolve the Mac control-plane API key without embedding it in source."""

    value = os.environ.get("IGP24_API_KEY", "").strip()
    if value:
        return value

    configured = os.environ.get("IGP24_API_KEY_FILE")
    key_path = Path(configured).expanduser() if configured else Path.home() / ".config/igp24/api_key"
    try:
        value = key_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        value = ""
    if value:
        return value
    raise ConfigurationError(
        "IGP24 API key is not configured; set IGP24_API_KEY or create "
        f"{key_path} with mode 0600"
    )
