from __future__ import annotations

import io
import json
import socket
from typing import Any, Dict
from urllib import error

import pytest

from braindrive_runtime.providers.base import ProviderChatRequest
from braindrive_runtime.providers.ollama import OllamaAdapter
from braindrive_runtime.providers.openrouter import OpenRouterAdapter
from braindrive_runtime.providers.resolver import resolve_provider_adapter


class _FakeHttpResponse:
    def __init__(self, body: Dict[str, Any]) -> None:
        self._raw = json.dumps(body, ensure_ascii=True).encode("utf-8")
        self.status = 200
        self.headers = {"Content-Type": "application/json"}

    def read(self) -> bytes:
        return self._raw

    def __enter__(self) -> "_FakeHttpResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


def _request(model: str = "anthropic/claude-sonnet-4") -> ProviderChatRequest:
    return ProviderChatRequest(
        model=model,
        prompt="hello",
        llm={},
        parent_message_id="msg-1",
    )


def test_openrouter_adapter_chat_success_with_tool_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = OpenRouterAdapter(
        base_url="https://openrouter.ai/api/v1",
        api_key="test-key",
        site_url="",
        app_name="BrainDrive-MVP",
        timeout_sec="30",
    )

    def _ok(req, timeout=0):  # noqa: ANN001
        assert req.full_url.endswith("/chat/completions")
        return _FakeHttpResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": "Hello from adapter",
                            "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "search"}}],
                        }
                    }
                ]
            }
        )

    monkeypatch.setattr("braindrive_runtime.providers.openrouter.request.urlopen", _ok)
    result, err = adapter.chat_completion(_request())

    assert err is None
    assert result is not None
    assert result.text == "Hello from adapter"
    assert len(result.tool_calls) == 1


def test_openrouter_adapter_unauthorized_error(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = OpenRouterAdapter(
        base_url="https://openrouter.ai/api/v1",
        api_key="test-key",
        site_url="",
        app_name="BrainDrive-MVP",
        timeout_sec="30",
    )

    def _unauthorized(req, timeout=0):  # noqa: ANN001
        raise error.HTTPError(req.full_url, 401, "Unauthorized", hdrs=None, fp=io.BytesIO(b"{}"))

    monkeypatch.setattr("braindrive_runtime.providers.openrouter.request.urlopen", _unauthorized)
    result, err = adapter.chat_completion(_request())

    assert result is None
    assert err is not None
    assert err["payload"]["error"]["code"] == "E_NODE_UNAVAILABLE"
    assert err["payload"]["error"]["retryable"] is False


def test_openrouter_adapter_timeout_retryable(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = OpenRouterAdapter(
        base_url="https://openrouter.ai/api/v1",
        api_key="test-key",
        site_url="",
        app_name="BrainDrive-MVP",
        timeout_sec="30",
    )

    def _timeout(req, timeout=0):  # noqa: ANN001
        raise error.URLError(socket.timeout("timed out"))

    monkeypatch.setattr("braindrive_runtime.providers.openrouter.request.urlopen", _timeout)
    result, err = adapter.chat_completion(_request())

    assert result is None
    assert err is not None
    assert err["payload"]["error"]["code"] == "E_NODE_TIMEOUT"
    assert err["payload"]["error"]["retryable"] is True


def test_openrouter_adapter_invalid_json_error(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = OpenRouterAdapter(
        base_url="https://openrouter.ai/api/v1",
        api_key="test-key",
        site_url="",
        app_name="BrainDrive-MVP",
        timeout_sec="30",
    )

    class _InvalidJsonResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def read(self) -> bytes:
            return b"not-json"

    monkeypatch.setattr("braindrive_runtime.providers.openrouter.request.urlopen", lambda req, timeout=0: _InvalidJsonResponse())
    result, err = adapter.chat_completion(_request())

    assert result is None
    assert err is not None
    assert err["payload"]["error"]["code"] == "E_NODE_ERROR"


def test_ollama_adapter_retryable_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = OllamaAdapter(
        base_url="http://localhost:11434/v1",
        api_key="",
        timeout_sec="30",
    )

    def _down(req, timeout=0):  # noqa: ANN001
        raise error.HTTPError(req.full_url, 503, "Unavailable", hdrs=None, fp=io.BytesIO(b"{}"))

    monkeypatch.setattr("braindrive_runtime.providers.ollama.request.urlopen", _down)
    result, err = adapter.chat_completion(ProviderChatRequest(model="llama3:8b", prompt="hello", llm={}, parent_message_id="msg-2"))

    assert result is None
    assert err is not None
    assert err["payload"]["error"]["code"] == "E_NODE_UNAVAILABLE"
    assert err["payload"]["error"]["retryable"] is True


def test_ollama_catalog_falls_back_on_upstream_error(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = OllamaAdapter(
        base_url="http://localhost:11434/v1",
        api_key="",
        timeout_sec="30",
    )

    def _unavailable(req, timeout=0):  # noqa: ANN001
        raise error.URLError("connection refused")

    monkeypatch.setattr("braindrive_runtime.providers.ollama.request.urlopen", _unavailable)
    catalog = adapter.catalog(parent_message_id="msg-catalog")

    assert catalog.fallback is True
    assert "llama3:8b" in catalog.models


def test_provider_resolver_builds_expected_adapter_types() -> None:
    openrouter = resolve_provider_adapter(
        "openrouter",
        {
            "BRAINDRIVE_OPENROUTER_API_KEY": "test-key",
            "BRAINDRIVE_OPENROUTER_BASE_URL": "https://openrouter.ai/api/v1",
        },
    )
    ollama = resolve_provider_adapter(
        "ollama",
        {
            "BRAINDRIVE_OLLAMA_BASE_URL": "http://localhost:11434/v1",
        },
    )
    assert isinstance(openrouter, OpenRouterAdapter)
    assert isinstance(ollama, OllamaAdapter)
