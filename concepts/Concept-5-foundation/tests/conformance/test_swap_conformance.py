from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, List

from braindrive_runtime.protocol import new_uuid
from braindrive_runtime.runtime import BrainDriveRuntime

REPORT_ROWS: List[Dict[str, Any]] = []


def _msg(intent: str, payload: Dict[str, Any], extensions: Dict[str, Any] | None = None) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "protocol_version": "0.1",
        "message_id": new_uuid(),
        "intent": intent,
        "payload": payload,
    }
    if extensions is not None:
        body["extensions"] = extensions
    return body


def _source_hashes() -> Dict[str, str]:
    repo = Path(__file__).resolve().parents[2]
    tracked = [
        repo / "braindrive_runtime" / "config.py",
        repo / "braindrive_runtime" / "nodes" / "model_openrouter.py",
        repo / "braindrive_runtime" / "nodes" / "model_ollama.py",
        repo / "braindrive_runtime" / "nodes" / "model_provider.py",
        repo / "braindrive_runtime" / "providers" / "openrouter.py",
        repo / "braindrive_runtime" / "providers" / "ollama.py",
        repo / "braindrive_runtime" / "providers" / "resolver.py",
        repo / "braindrive_runtime" / "providers" / "registry.py",
        repo / "braindrive_runtime" / "nodes" / "scrapling.py",
    ]
    out: Dict[str, str] = {}
    for path in tracked:
        out[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def _record(name: str, detail: str) -> None:
    REPORT_ROWS.append({"scenario": name, "status": "pass", "detail": detail})


def test_provider_swap_is_config_only(runtime) -> None:
    before = _source_hashes()

    openrouter = runtime.route(
        _msg(
            "model.chat.complete",
            {"prompt": "provider swap check"},
            {"llm": {"provider": "openrouter", "model": "anthropic/claude-sonnet-4"}},
        )
    )
    ollama = runtime.route(
        _msg(
            "model.chat.complete",
            {"prompt": "provider swap check"},
            {"llm": {"provider": "ollama", "model": "llama3:8b"}},
        )
    )

    assert openrouter["intent"] == "model.chat.completed"
    assert ollama["intent"] == "model.chat.completed"
    assert openrouter["payload"]["provider"] == "openrouter"
    assert ollama["payload"]["provider"] == "ollama"
    assert _source_hashes() == before

    _record("provider_swap", "OpenRouter <-> Ollama succeeded with no source edits")


def test_model_swap_within_provider_is_config_only(runtime) -> None:
    before = _source_hashes()

    first = runtime.route(
        _msg(
            "model.chat.complete",
            {"prompt": "model one"},
            {"llm": {"provider": "openrouter", "model": "anthropic/claude-sonnet-4"}},
        )
    )
    second = runtime.route(
        _msg(
            "model.chat.complete",
            {"prompt": "model two"},
            {"llm": {"provider": "openrouter", "model": "openai/gpt-4.1-mini"}},
        )
    )

    assert first["intent"] == "model.chat.completed"
    assert second["intent"] == "model.chat.completed"
    assert first["payload"]["model"] == "anthropic/claude-sonnet-4"
    assert second["payload"]["model"] == "openai/gpt-4.1-mini"
    assert _source_hashes() == before

    _record("model_swap", "Model override inside one provider stayed config-only")


def test_tool_backend_swap_is_config_only(runtime) -> None:
    before = _source_hashes()
    node = runtime.nodes["node.web.scrapling"].node

    class _BackendA:
        def get(self, **kwargs):
            return {"status": 200, "url": kwargs["url"], "content": ["alpha"]}

    class _BackendB:
        def get(self, **kwargs):
            return {"status": 200, "url": kwargs["url"], "content": ["beta"]}

    node.backend = _BackendA()
    first = runtime.route(
        _msg(
            "web.scrape.get",
            {
                "url": "https://example.com",
                "save_to_library": False,
            },
        )
    )

    node.backend = _BackendB()
    second = runtime.route(
        _msg(
            "web.scrape.get",
            {
                "url": "https://example.com",
                "save_to_library": False,
            },
        )
    )

    assert first["intent"] == "web.scrape.completed"
    assert second["intent"] == "web.scrape.completed"
    first_content = first["payload"]["results"][0]["content"][0]
    second_content = second["payload"]["results"][0]["content"][0]
    assert first_content == "alpha"
    assert second_content == "beta"
    assert _source_hashes() == before

    _record("tool_backend_swap", "Scrapling backend changed without core runtime source edits")


def test_provider_c_extension_is_config_only(tmp_path) -> None:
    before = _source_hashes()

    plugin_dir = tmp_path / "provider-plugin"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "synthetic_provider.py").write_text(
        "\n".join(
            [
                "from __future__ import annotations",
                "",
                "from braindrive_runtime.providers.base import ProviderAdapter, ProviderCatalogResult, ProviderChatResult",
                "",
                "class SyntheticAdapter(ProviderAdapter):",
                "    provider_name = 'synthetic'",
                "",
                "    def __init__(self, response_prefix: str = 'synthetic') -> None:",
                "        self.response_prefix = response_prefix",
                "",
                "    def validate_catalog(self, parent_message_id):",
                "        return None",
                "",
                "    def validate(self, request):",
                "        return None",
                "",
                "    def chat_completion(self, request):",
                "        text = f\"{self.response_prefix}:{request.model}:{request.prompt}\"",
                "        return ProviderChatResult(text=text), None",
                "",
                "    def catalog(self, parent_message_id):",
                "        return ProviderCatalogResult(models=['synthetic/default'], fallback=False)",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    registry_dir = tmp_path / "provider-registry"
    registry_dir.mkdir(parents=True, exist_ok=True)
    (registry_dir / "synthetic.json").write_text(
        json.dumps(
            {
                "provider": "synthetic",
                "adapter_factory": "synthetic_provider:SyntheticAdapter",
                "adapter_kwargs": {
                    "response_prefix": {
                        "env": "BRAINDRIVE_SYNTHETIC_RESPONSE_PREFIX",
                        "default": "synthetic"
                    }
                },
                "model_node": {
                    "node_id": "node.model.synthetic",
                    "priority": 150,
                    "label": "Synthetic"
                },
                "config": {
                    "base_url_env": "",
                    "base_url_default": "",
                    "base_url_required": False,
                    "default_model_env": "BRAINDRIVE_SYNTHETIC_DEFAULT_MODEL",
                    "required_env": [],
                    "required_env_messages": {},
                    "startup_notice": "synthetic provider adapter"
                }
            },
            ensure_ascii=True,
            indent=2,
        ),
        encoding="utf-8",
    )

    user_config = tmp_path / "user-config.yaml"
    user_config.write_text(
        "\n".join(
            [
                "llm:",
                "  default_provider: synthetic",
                "  synthetic:",
                "    default_model: synthetic/default",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    import sys

    added_path = False
    if str(plugin_dir) not in sys.path:
        sys.path.insert(0, str(plugin_dir))
        added_path = True
    try:
        runtime = BrainDriveRuntime(
            library_root=tmp_path / "library",
            data_root=tmp_path / "runtime-data",
            user_config_path=user_config,
            env={
                "BRAINDRIVE_PROVIDER_REGISTRY_DIR": str(registry_dir),
                "BRAINDRIVE_SYNTHETIC_RESPONSE_PREFIX": "fixture",
                "BRAINDRIVE_DEFAULT_PROVIDER": "synthetic",
                "BRAINDRIVE_OPENROUTER_API_KEY": "test-key",
                "BRAINDRIVE_OPENROUTER_DEFAULT_MODEL": "anthropic/claude-sonnet-4",
                "BRAINDRIVE_OLLAMA_BASE_URL": "http://localhost:11434/v1",
                "BRAINDRIVE_OLLAMA_DEFAULT_MODEL": "llama3:8b",
            },
        )
        runtime.bootstrap()

        response = runtime.route(_msg("model.chat.complete", {"prompt": "provider c check"}))
        assert response["intent"] == "model.chat.completed"
        assert response["payload"]["provider"] == "synthetic"
        assert response["payload"]["model"] == "synthetic/default"
        assert response["payload"]["text"] == "fixture:synthetic/default:provider c check"
        assert _source_hashes() == before
    finally:
        if added_path:
            sys.path.remove(str(plugin_dir))

    _record("provider_extension", "Synthetic provider added via adapter+config fixture only")


def test_zzz_write_conformance_report_artifact(tmp_path) -> None:
    assert len(REPORT_ROWS) >= 3
    report = {
        "suite": "D147-style config-only swap conformance",
        "scenarios": REPORT_ROWS,
    }

    report_path = os.getenv("BRAINDRIVE_CONFORMANCE_REPORT", "")
    if report_path:
        target = Path(report_path)
    else:
        target = tmp_path / "conformance-report.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8")

    assert target.exists()
