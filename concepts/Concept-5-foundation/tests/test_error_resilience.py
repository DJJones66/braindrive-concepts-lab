from __future__ import annotations

from pathlib import Path

from braindrive_runtime.protocol import new_uuid
from braindrive_runtime.runtime import BrainDriveRuntime


def _msg(intent, payload, extensions=None):
    body = {
        "protocol_version": "0.1",
        "message_id": new_uuid(),
        "intent": intent,
        "payload": payload,
    }
    if extensions is not None:
        body["extensions"] = extensions
    return body


def test_missing_openrouter_key_fails_clearly(runtime_no_openrouter_key, make_message):
    response = runtime_no_openrouter_key.route(
        make_message("model.chat.complete", {"prompt": "hello"}, {"llm": {"provider": "openrouter", "model": "anthropic/claude-sonnet-4"}})
    )
    assert response["intent"] == "error"
    assert response["payload"]["error"]["code"] == "E_NODE_UNAVAILABLE"
    assert "BRAINDRIVE_OPENROUTER_API_KEY" in response["payload"]["error"]["message"]


def test_missing_provider_default_model_fails_clearly(tmp_path: Path):
    runtime = BrainDriveRuntime(
        library_root=tmp_path / "library",
        data_root=tmp_path / "runtime-data",
        env={
            "BRAINDRIVE_DEFAULT_PROVIDER": "ollama",
            "BRAINDRIVE_OLLAMA_BASE_URL": "http://localhost:11434/v1",
            "BRAINDRIVE_OLLAMA_DEFAULT_MODEL": "",
            "BRAINDRIVE_OPENROUTER_API_KEY": "test-key",
            "BRAINDRIVE_OPENROUTER_DEFAULT_MODEL": "anthropic/claude-sonnet-4",
        },
    )
    runtime.bootstrap()

    response = runtime.route(_msg("model.chat.complete", {"prompt": "hello"}))
    assert response["intent"] == "error"
    assert response["payload"]["error"]["code"] == "E_BAD_MESSAGE"


def test_missing_ollama_base_url_fails_when_selected(tmp_path: Path):
    runtime = BrainDriveRuntime(
        library_root=tmp_path / "library",
        data_root=tmp_path / "runtime-data",
        env={
            "BRAINDRIVE_DEFAULT_PROVIDER": "ollama",
            "BRAINDRIVE_OLLAMA_BASE_URL": "",
            "BRAINDRIVE_OLLAMA_DEFAULT_MODEL": "llama3:8b",
            "BRAINDRIVE_OPENROUTER_API_KEY": "test-key",
            "BRAINDRIVE_OPENROUTER_DEFAULT_MODEL": "anthropic/claude-sonnet-4",
        },
    )
    runtime.bootstrap()

    response = runtime.route(_msg("model.chat.complete", {"prompt": "hello"}))
    assert response["intent"] == "error"
    assert response["payload"]["error"]["code"] == "E_NODE_UNAVAILABLE"
    assert "BRAINDRIVE_OLLAMA_BASE_URL" in response["payload"]["error"]["message"]


def test_provider_timeout_is_retryable(runtime_ollama_default, make_message):
    response = runtime_ollama_default.route(
        make_message("model.chat.complete", {"prompt": "hello", "simulate_timeout": True})
    )
    assert response["intent"] == "error"
    assert response["payload"]["error"]["retryable"] is True


def test_ollama_optional_api_key_can_be_empty(runtime_ollama_default, make_message):
    response = runtime_ollama_default.route(make_message("model.chat.complete", {"prompt": "hello"}))
    assert response["intent"] == "model.chat.completed"
    assert response["payload"]["provider"] == "ollama"


def test_provider_override_and_default_precedence(tmp_path: Path):
    user_config = tmp_path / "user-config.yaml"
    user_config.write_text(
        """
llm:
  default_provider: ollama
  ollama:
    base_url: http://localhost:11434/v1
    default_model: llama3:8b
  openrouter:
    base_url: https://openrouter.ai/api/v1
    default_model: anthropic/claude-sonnet-4
""".strip()
        + "\n",
        encoding="utf-8",
    )

    runtime = BrainDriveRuntime(
        library_root=tmp_path / "library",
        data_root=tmp_path / "runtime-data",
        user_config_path=user_config,
        env={
            "BRAINDRIVE_DEFAULT_PROVIDER": "openrouter",
            "BRAINDRIVE_OPENROUTER_API_KEY": "test-key",
            "BRAINDRIVE_OPENROUTER_DEFAULT_MODEL": "anthropic/claude-sonnet-4",
            "BRAINDRIVE_OLLAMA_BASE_URL": "http://localhost:11434/v1",
            "BRAINDRIVE_OLLAMA_DEFAULT_MODEL": "llama3:8b",
        },
    )
    runtime.bootstrap()

    default_route = runtime.route(_msg("model.chat.complete", {"prompt": "hello"}))
    assert default_route["intent"] == "model.chat.completed"
    assert default_route["payload"]["provider"] == "ollama"

    override_route = runtime.route(
        _msg(
            "model.chat.complete",
            {"prompt": "hello"},
            {"llm": {"provider": "openrouter", "model": "anthropic/claude-sonnet-4"}},
        )
    )
    assert override_route["intent"] == "model.chat.completed"
    assert override_route["payload"]["provider"] == "openrouter"


def test_missing_confirmation_returns_confirmation_error(runtime, make_message):
    response = runtime.route(make_message("memory.delete.propose", {"path": "missing.md"}))
    assert response["intent"] == "error"
    assert response["payload"]["error"]["code"] == "E_CONFIRMATION_REQUIRED"


def test_provider_selection_notice_has_no_secret(runtime):
    result = runtime.bootstrap()
    notice = result["provider_notice"]
    assert "test-key" not in notice
    assert "provider=" in notice


def test_context_compaction_notice(runtime, make_message):
    response = runtime.route(make_message("runtime.compact_context", {}))
    assert response["intent"] == "runtime.context_compacted"
    assert response["payload"]["compacted"] is True
