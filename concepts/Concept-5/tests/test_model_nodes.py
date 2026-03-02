from __future__ import annotations

import io
import socket
from urllib import error

from braindrive_runtime.nodes import model_ollama, model_openrouter


def test_openrouter_completion_returns_provider_text(runtime, make_message):
    response = runtime.route(
        make_message(
            "model.chat.complete",
            {"prompt": "tell me a joke"},
            {"llm": {"provider": "openrouter", "model": "anthropic/claude-sonnet-4"}},
        )
    )
    assert response["intent"] == "model.chat.completed"
    assert response["payload"]["provider"] == "openrouter"
    assert response["payload"]["text"] == "mock-response:anthropic/claude-sonnet-4:tell me a joke"


def test_openrouter_http_unauthorized_maps_to_unavailable(runtime, make_message, monkeypatch):
    def _unauthorized(req, timeout=0):  # noqa: ANN001
        raise error.HTTPError(
            req.full_url,
            401,
            "Unauthorized",
            hdrs=None,
            fp=io.BytesIO(b'{"error":{"message":"invalid key"}}'),
        )

    monkeypatch.setattr(model_openrouter.request, "urlopen", _unauthorized)

    response = runtime.route(
        make_message(
            "model.chat.complete",
            {"prompt": "hello"},
            {"llm": {"provider": "openrouter", "model": "anthropic/claude-sonnet-4"}},
        )
    )
    assert response["intent"] == "error"
    assert response["payload"]["error"]["code"] == "E_NODE_UNAVAILABLE"
    assert response["payload"]["error"]["retryable"] is False


def test_ollama_timeout_maps_to_retryable_timeout(runtime, make_message, monkeypatch):
    def _timeout(req, timeout=0):  # noqa: ANN001
        raise error.URLError(socket.timeout("timed out"))

    monkeypatch.setattr(model_ollama.request, "urlopen", _timeout)

    response = runtime.route(
        make_message(
            "model.chat.complete",
            {"prompt": "hello"},
            {"llm": {"provider": "ollama", "model": "llama3:8b"}},
        )
    )
    assert response["intent"] == "error"
    assert response["payload"]["error"]["code"] == "E_NODE_TIMEOUT"
    assert response["payload"]["error"]["retryable"] is True
