from __future__ import annotations

from typing import Optional

LEGACY_DEFAULT_REGISTRATION_TOKEN = "braindrive-mvp-dev-token"


def _clean_env_value(value: Optional[str]) -> str:
    raw = (value or "").strip()
    if raw.startswith(("'", '"')) and raw.endswith(("'", '"')) and len(raw) >= 2:
        raw = raw[1:-1]
    return raw.strip()


def validate_registration_token(value: Optional[str]) -> str:
    token = _clean_env_value(value)
    if not token:
        raise ValueError("ROUTER_REGISTRATION_TOKEN is required and cannot be empty.")
    if token == LEGACY_DEFAULT_REGISTRATION_TOKEN:
        raise ValueError(
            "ROUTER_REGISTRATION_TOKEN uses insecure legacy default value and must be replaced."
        )
    return token


def is_loopback_bind_address(value: Optional[str]) -> bool:
    bind = _clean_env_value(value).lower()
    return bind in {"127.0.0.1", "localhost", "::1", "[::1]"}
