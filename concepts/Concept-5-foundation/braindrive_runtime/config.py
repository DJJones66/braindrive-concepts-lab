from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

from .constants import MODEL_PROVIDER_OPENROUTER
from .providers.registry import list_supported_providers, provider_config_spec


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
        supported = set(list_supported_providers(self.env))
        llm_cfg = self.user_config.get("llm", {}) if isinstance(self.user_config.get("llm"), dict) else {}
        cfg_provider = llm_cfg.get("default_provider")
        if isinstance(cfg_provider, str) and cfg_provider.strip().lower() in supported:
            return cfg_provider.strip().lower(), "user config"

        env_provider = self.env.get("BRAINDRIVE_DEFAULT_PROVIDER", MODEL_PROVIDER_OPENROUTER).strip().lower()
        if env_provider in supported:
            return env_provider, ".env"

        if MODEL_PROVIDER_OPENROUTER in supported:
            return MODEL_PROVIDER_OPENROUTER, "fallback"
        if supported:
            return sorted(supported)[0], "fallback"
        return MODEL_PROVIDER_OPENROUTER, "fallback"

    def provider_defaults(self, provider: str) -> ProviderDefaults:
        llm_cfg = self.user_config.get("llm", {}) if isinstance(self.user_config.get("llm"), dict) else {}
        cfg_provider = llm_cfg.get(provider, {}) if isinstance(llm_cfg.get(provider), dict) else {}
        spec = provider_config_spec(provider, self.env)

        base = str(cfg_provider.get("base_url") or self.env.get(spec.base_url_env, spec.base_url_default)).strip()
        model = str(cfg_provider.get("default_model") or self.env.get(spec.default_model_env, "")).strip()
        return ProviderDefaults(base_url=base, default_model=model)

    def select_llm(self, llm_extension: Optional[Dict[str, Any]]) -> LLMSelection:
        ext = llm_extension if isinstance(llm_extension, dict) else {}
        supported = set(list_supported_providers(self.env))

        provider: str
        provider_source: str
        requested_provider = str(ext.get("provider", "")).strip().lower()
        if requested_provider and requested_provider in supported:
            provider = requested_provider
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
        spec = provider_config_spec(selection.provider, self.env)
        for env_name in spec.required_env:
            if not self.env.get(env_name, "").strip():
                return spec.required_env_messages.get(
                    env_name, f"{env_name} is required for provider {selection.provider}"
                )

        if spec.base_url_required and not self.provider_defaults(selection.provider).base_url:
            if spec.base_url_env:
                return f"{spec.base_url_env} is required for provider {selection.provider}"
            return f"Base URL is required for provider {selection.provider}"

        if not selection.model:
            return f"Default model is required for provider {selection.provider}"
        return None

    def startup_provider_notice(self, selection: LLMSelection) -> str:
        base = f"active provider={selection.provider} ({selection.provider_source}), model={selection.model} ({selection.model_source})"
        notice = provider_config_spec(selection.provider, self.env).startup_notice
        if notice:
            return base + f"; {notice}"
        return base


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
