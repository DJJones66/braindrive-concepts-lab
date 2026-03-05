from __future__ import annotations

from typing import Mapping

from .base import ProviderAdapter
from .registry import resolve_provider_adapter as _resolve_with_registry


def resolve_provider_adapter(provider: str, env: Mapping[str, str]) -> ProviderAdapter:
    source = dict(env or {})
    return _resolve_with_registry(provider, source)
