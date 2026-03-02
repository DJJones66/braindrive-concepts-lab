from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

from .constants import MODEL_PROVIDER_OLLAMA, MODEL_PROVIDER_OPENROUTER, MODEL_PROVIDERS


@dataclass
class ProviderDefaults:
    base_url: str
    default_model: str


@dataclass
class LLMSelection:
    provider: str
    model: str
    provider_source: str
    model_source: str


class ConfigResolver:
    def __init__(self, env: Optional[Mapping[str, str]] = None, user_config_path: Optional[Path] = None) -> None:
        self.env = dict(env or os.environ)
        self.user_config_path = user_config_path or Path.home() / ".braindrive" / "config.yaml"
        self.user_config = _load_config_yaml(self.user_config_path)

    def default_provider(self) -> Tuple[str, str]:
        llm_cfg = self.user_config.get("llm", {}) if isinstance(self.user_config.get("llm"), dict) else {}
        cfg_provider = llm_cfg.get("default_provider")
        if isinstance(cfg_provider, str) and cfg_provider in MODEL_PROVIDERS:
            return cfg_provider, "user config"

        env_provider = self.env.get("BRAINDRIVE_DEFAULT_PROVIDER", "openrouter").strip().lower()
        if env_provider in MODEL_PROVIDERS:
            return env_provider, ".env"

        return MODEL_PROVIDER_OPENROUTER, "fallback"

    def provider_defaults(self, provider: str) -> ProviderDefaults:
        llm_cfg = self.user_config.get("llm", {}) if isinstance(self.user_config.get("llm"), dict) else {}
        cfg_provider = llm_cfg.get(provider, {}) if isinstance(llm_cfg.get(provider), dict) else {}

        if provider == MODEL_PROVIDER_OPENROUTER:
            base = str(
                cfg_provider.get("base_url")
                or self.env.get("BRAINDRIVE_OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
            )
            model = str(
                cfg_provider.get("default_model")
                or self.env.get("BRAINDRIVE_OPENROUTER_DEFAULT_MODEL", "")
            ).strip()
            return ProviderDefaults(base_url=base, default_model=model)

        base = str(
            cfg_provider.get("base_url")
            or self.env.get("BRAINDRIVE_OLLAMA_BASE_URL", "")
        ).strip()
        model = str(
            cfg_provider.get("default_model")
            or self.env.get("BRAINDRIVE_OLLAMA_DEFAULT_MODEL", "")
        ).strip()
        return ProviderDefaults(base_url=base, default_model=model)

    def select_llm(self, llm_extension: Optional[Dict[str, Any]]) -> LLMSelection:
        ext = llm_extension if isinstance(llm_extension, dict) else {}

        provider: str
        provider_source: str
        if isinstance(ext.get("provider"), str) and ext["provider"] in MODEL_PROVIDERS:
            provider = ext["provider"]
            provider_source = "request override"
        else:
            provider, provider_source = self.default_provider()

        provider_cfg = self.provider_defaults(provider)

        if isinstance(ext.get("model"), str) and ext["model"].strip():
            model = ext["model"].strip()
            model_source = "request override"
        else:
            llm_cfg = self.user_config.get("llm", {}) if isinstance(self.user_config.get("llm"), dict) else {}
            provider_cfg_dict = llm_cfg.get(provider, {}) if isinstance(llm_cfg.get(provider), dict) else {}
            if isinstance(provider_cfg_dict.get("default_model"), str) and provider_cfg_dict.get("default_model", "").strip():
                model = provider_cfg_dict["default_model"].strip()
                model_source = "user config"
            else:
                model = provider_cfg.default_model
                model_source = ".env"

        return LLMSelection(
            provider=provider,
            model=model,
            provider_source=provider_source,
            model_source=model_source,
        )

    def validate_provider_requirements(self, selection: LLMSelection) -> Optional[str]:
        if selection.provider == MODEL_PROVIDER_OPENROUTER:
            if not self.env.get("BRAINDRIVE_OPENROUTER_API_KEY", "").strip():
                return "BRAINDRIVE_OPENROUTER_API_KEY is required for provider openrouter"
            if not selection.model:
                return "Default model is required for provider openrouter"
            return None

        if not self.provider_defaults(MODEL_PROVIDER_OLLAMA).base_url:
            return "BRAINDRIVE_OLLAMA_BASE_URL is required for provider ollama"
        if not selection.model:
            return "Default model is required for provider ollama"
        return None

    def startup_provider_notice(self, selection: LLMSelection) -> str:
        base = f"active provider={selection.provider} ({selection.provider_source}), model={selection.model} ({selection.model_source})"
        if selection.provider == MODEL_PROVIDER_OPENROUTER:
            return base + "; requires BRAINDRIVE_OPENROUTER_API_KEY"
        return base + "; requires BRAINDRIVE_OLLAMA_BASE_URL, BRAINDRIVE_OLLAMA_API_KEY is optional"


def _load_config_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}

    raw = path.read_text(encoding="utf-8")
    parsed = _parse_simple_yaml(raw)
    return parsed if isinstance(parsed, dict) else {}


def _parse_simple_yaml(text: str) -> Dict[str, Any]:
    root: Dict[str, Any] = {}
    stack: list[tuple[int, Dict[str, Any]]] = [(-1, root)]

    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        line = raw_line.strip()
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()

        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1] if stack else root

        if not value:
            node: Dict[str, Any] = {}
            parent[key] = node
            stack.append((indent, node))
            continue

        clean_value = value.strip().strip("\"'")
        parent[key] = clean_value

    return root
