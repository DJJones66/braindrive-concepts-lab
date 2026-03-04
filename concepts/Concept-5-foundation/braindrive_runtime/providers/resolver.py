from __future__ import annotations

from typing import Mapping

from ..constants import MODEL_PROVIDER_OLLAMA, MODEL_PROVIDER_OPENROUTER
from .base import ProviderAdapter
from .ollama import OllamaAdapter
from .openrouter import OpenRouterAdapter


def resolve_provider_adapter(provider: str, env: Mapping[str, str]) -> ProviderAdapter:
    source = env if env is not None else {}

    if provider == MODEL_PROVIDER_OPENROUTER:
        return OpenRouterAdapter(
            base_url=str(source.get("BRAINDRIVE_OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")),
            api_key=str(source.get("BRAINDRIVE_OPENROUTER_API_KEY", "")),
            site_url=str(source.get("BRAINDRIVE_OPENROUTER_SITE_URL", "")),
            app_name=str(source.get("BRAINDRIVE_OPENROUTER_APP_NAME", "BrainDrive-MVP")),
            timeout_sec=str(source.get("BRAINDRIVE_MODEL_TIMEOUT_SEC", "30")),
        )

    if provider == MODEL_PROVIDER_OLLAMA:
        return OllamaAdapter(
            base_url=str(source.get("BRAINDRIVE_OLLAMA_BASE_URL", "")),
            api_key=str(source.get("BRAINDRIVE_OLLAMA_API_KEY", "")),
            timeout_sec=str(source.get("BRAINDRIVE_MODEL_TIMEOUT_SEC", "30")),
        )

    raise ValueError(f"Unsupported provider: {provider}")
