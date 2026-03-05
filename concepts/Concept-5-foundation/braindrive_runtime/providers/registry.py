from __future__ import annotations

import importlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Tuple

from .base import ProviderAdapter


@dataclass(frozen=True)
class EnvValueSpec:
    env: str
    default: str = ""


@dataclass(frozen=True)
class ProviderConfigSpec:
    base_url_env: str
    base_url_default: str
    base_url_required: bool
    default_model_env: str
    required_env: Tuple[str, ...]
    required_env_messages: Dict[str, str]
    startup_notice: str


@dataclass(frozen=True)
class ModelProviderSpec:
    provider: str
    node_id: str
    priority: int
    label: str


@dataclass(frozen=True)
class ProviderRegistryEntry:
    provider: str
    adapter_factory: str
    adapter_kwargs: Dict[str, EnvValueSpec]
    config: ProviderConfigSpec
    model_node: ModelProviderSpec


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return default


def _provider_dirs(env: Mapping[str, str]) -> list[Path]:
    builtin = Path(__file__).resolve().parent / "adapters"
    dirs = [builtin]
    raw = str(env.get("BRAINDRIVE_PROVIDER_REGISTRY_DIR", "")).strip()
    if not raw:
        return dirs
    for token in raw.split(os.pathsep):
        value = token.strip()
        if value:
            dirs.append(Path(value))
    return dirs


def _load_manifest(path: Path) -> Dict[str, Any]:
    raw = path.read_text(encoding="utf-8")
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError(f"Provider manifest must be an object: {path}")
    return parsed


def _parse_env_value_spec(raw: Any) -> EnvValueSpec:
    if isinstance(raw, str) and raw.strip():
        return EnvValueSpec(env=raw.strip(), default="")
    if isinstance(raw, dict):
        env_name = str(raw.get("env", "")).strip()
        if not env_name:
            raise ValueError("adapter_kwargs entry requires non-empty 'env'")
        default = str(raw.get("default", ""))
        return EnvValueSpec(env=env_name, default=default)
    raise ValueError("adapter_kwargs entries must be a string or object")


def _entry_from_manifest(path: Path, payload: Dict[str, Any]) -> ProviderRegistryEntry:
    provider = str(payload.get("provider", "")).strip().lower()
    if not provider:
        raise ValueError(f"Provider manifest missing provider id: {path}")

    adapter_factory = str(payload.get("adapter_factory", "")).strip()
    if ":" not in adapter_factory:
        raise ValueError(f"Provider manifest has invalid adapter_factory: {path}")

    raw_kwargs = payload.get("adapter_kwargs", {})
    adapter_kwargs: Dict[str, EnvValueSpec] = {}
    if isinstance(raw_kwargs, dict):
        for key, value in raw_kwargs.items():
            name = str(key).strip()
            if not name:
                continue
            adapter_kwargs[name] = _parse_env_value_spec(value)

    model_node_raw = payload.get("model_node", {})
    if not isinstance(model_node_raw, dict):
        model_node_raw = {}
    node_id = str(model_node_raw.get("node_id", f"node.model.{provider}")).strip()
    if not node_id:
        node_id = f"node.model.{provider}"
    priority_raw = model_node_raw.get("priority", 160)
    try:
        priority = int(priority_raw)
    except (TypeError, ValueError):
        priority = 160
    label = str(model_node_raw.get("label", provider)).strip() or provider

    config_raw = payload.get("config", {})
    if not isinstance(config_raw, dict):
        config_raw = {}
    base_url_env = str(config_raw.get("base_url_env", "")).strip()
    base_url_default = str(config_raw.get("base_url_default", "")).strip()
    default_model_env = str(config_raw.get("default_model_env", "")).strip()
    required_env: Tuple[str, ...]
    req_values: list[str] = []
    raw_required_env = config_raw.get("required_env", [])
    if isinstance(raw_required_env, list):
        for value in raw_required_env:
            if isinstance(value, str) and value.strip():
                req_values.append(value.strip())
    required_env = tuple(req_values)

    raw_required_messages = config_raw.get("required_env_messages", {})
    required_env_messages: Dict[str, str] = {}
    if isinstance(raw_required_messages, dict):
        for key, value in raw_required_messages.items():
            env_name = str(key).strip()
            if not env_name:
                continue
            required_env_messages[env_name] = str(value).strip()

    config = ProviderConfigSpec(
        base_url_env=base_url_env,
        base_url_default=base_url_default,
        base_url_required=_as_bool(config_raw.get("base_url_required", False)),
        default_model_env=default_model_env,
        required_env=required_env,
        required_env_messages=required_env_messages,
        startup_notice=str(config_raw.get("startup_notice", "")).strip(),
    )
    node_spec = ModelProviderSpec(provider=provider, node_id=node_id, priority=priority, label=label)
    return ProviderRegistryEntry(
        provider=provider,
        adapter_factory=adapter_factory,
        adapter_kwargs=adapter_kwargs,
        config=config,
        model_node=node_spec,
    )


def load_provider_registry(env: Mapping[str, str] | None = None) -> Dict[str, ProviderRegistryEntry]:
    source = dict(env or {})
    entries: Dict[str, ProviderRegistryEntry] = {}
    for directory in _provider_dirs(source):
        if not directory.exists() or not directory.is_dir():
            continue
        for manifest in sorted(directory.glob("*.json")):
            try:
                payload = _load_manifest(manifest)
                entry = _entry_from_manifest(manifest, payload)
            except Exception as exc:
                raise ValueError(f"Invalid provider manifest {manifest}: {exc}") from exc
            entries[entry.provider] = entry
    if not entries:
        raise ValueError("No provider manifests were loaded")
    return entries


def list_supported_providers(env: Mapping[str, str] | None = None) -> list[str]:
    entries = load_provider_registry(env)
    return sorted(entries.keys())


def provider_registry_entry(provider: str, env: Mapping[str, str] | None = None) -> ProviderRegistryEntry:
    entries = load_provider_registry(env)
    key = str(provider).strip().lower()
    entry = entries.get(key)
    if entry is None:
        raise ValueError(f"Unsupported provider: {provider}")
    return entry


def provider_config_spec(provider: str, env: Mapping[str, str] | None = None) -> ProviderConfigSpec:
    return provider_registry_entry(provider, env).config


def list_model_provider_specs(env: Mapping[str, str] | None = None) -> list[ModelProviderSpec]:
    entries = load_provider_registry(env)
    out = [entries[key].model_node for key in sorted(entries.keys())]
    return out


def _import_factory(factory_ref: str):
    module_name, _, attr_name = factory_ref.partition(":")
    if not module_name or not attr_name:
        raise ValueError(f"Invalid adapter factory reference: {factory_ref}")
    module = importlib.import_module(module_name)
    factory = getattr(module, attr_name, None)
    if factory is None or not callable(factory):
        raise ValueError(f"Adapter factory not callable: {factory_ref}")
    return factory


def resolve_provider_adapter(provider: str, env: Mapping[str, str]) -> ProviderAdapter:
    source = dict(env or {})
    entry = provider_registry_entry(provider, source)
    factory = _import_factory(entry.adapter_factory)

    kwargs: Dict[str, Any] = {}
    for key, spec in entry.adapter_kwargs.items():
        kwargs[key] = str(source.get(spec.env, spec.default))

    adapter = factory(**kwargs)
    if not isinstance(adapter, ProviderAdapter):
        raise TypeError(f"Adapter factory did not return ProviderAdapter for provider {provider}")
    return adapter

