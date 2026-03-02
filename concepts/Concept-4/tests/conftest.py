from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

from braindrive_runtime.nodes import model_ollama, model_openrouter
from braindrive_runtime.protocol import new_uuid
from braindrive_runtime.runtime import BrainDriveRuntime


class _FakeHttpResponse:
    def __init__(self, body: Dict[str, Any]) -> None:
        self._raw = json.dumps(body, ensure_ascii=True).encode("utf-8")

    def read(self) -> bytes:
        return self._raw

    def __enter__(self) -> "_FakeHttpResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


def _prompt_from_messages(messages: Any) -> str:
    if not isinstance(messages, list):
        return ""
    for item in messages:
        if not isinstance(item, dict):
            continue
        if str(item.get("role", "")).lower() != "user":
            continue
        content = item.get("content")
        if isinstance(content, str):
            return content
    return ""


@pytest.fixture(autouse=True)
def mock_model_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_urlopen(req, timeout=0):  # noqa: ANN001
        url = str(getattr(req, "full_url", ""))
        method = str(req.get_method() if hasattr(req, "get_method") else "GET").upper()

        body: Dict[str, Any] = {}
        data = getattr(req, "data", None)
        if isinstance(data, (bytes, bytearray)) and data:
            body = json.loads(data.decode("utf-8"))

        if method == "GET" and url.endswith("/models"):
            if "openrouter" in url:
                return _FakeHttpResponse(
                    {"data": [{"id": "anthropic/claude-sonnet-4"}, {"id": "openai/gpt-4.1-mini"}]}
                )
            return _FakeHttpResponse({"data": [{"id": "llama3:8b"}, {"id": "mistral:7b"}]})

        if method == "POST" and url.endswith("/chat/completions"):
            model = str(body.get("model", "unknown"))
            prompt = _prompt_from_messages(body.get("messages"))
            return _FakeHttpResponse(
                {
                    "id": "chatcmpl-mock",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": f"mock-response:{model}:{prompt}"},
                            "finish_reason": "stop",
                        }
                    ],
                }
            )

        raise AssertionError(f"Unexpected provider request during tests: {method} {url}")

    monkeypatch.setattr(model_openrouter.request, "urlopen", _fake_urlopen)
    monkeypatch.setattr(model_ollama.request, "urlopen", _fake_urlopen)


@pytest.fixture
def runtime(tmp_path: Path) -> BrainDriveRuntime:
    library = tmp_path / "library"
    data = tmp_path / "runtime-data"
    rt = BrainDriveRuntime(
        library_root=library,
        data_root=data,
        env={
            "BRAINDRIVE_DEFAULT_PROVIDER": "openrouter",
            "BRAINDRIVE_OPENROUTER_API_KEY": "test-key",
            "BRAINDRIVE_OPENROUTER_DEFAULT_MODEL": "anthropic/claude-sonnet-4",
            "BRAINDRIVE_OLLAMA_BASE_URL": "http://localhost:11434/v1",
            "BRAINDRIVE_OLLAMA_DEFAULT_MODEL": "llama3:8b",
            "BRAINDRIVE_ENABLE_TEST_ENDPOINTS": "true",
        },
    )
    rt.bootstrap()
    return rt


@pytest.fixture
def runtime_no_openrouter_key(tmp_path: Path) -> BrainDriveRuntime:
    library = tmp_path / "library"
    data = tmp_path / "runtime-data"
    rt = BrainDriveRuntime(
        library_root=library,
        data_root=data,
        env={
            "BRAINDRIVE_DEFAULT_PROVIDER": "openrouter",
            "BRAINDRIVE_OPENROUTER_API_KEY": "",
            "BRAINDRIVE_OPENROUTER_DEFAULT_MODEL": "anthropic/claude-sonnet-4",
            "BRAINDRIVE_OLLAMA_BASE_URL": "http://localhost:11434/v1",
            "BRAINDRIVE_OLLAMA_DEFAULT_MODEL": "llama3:8b",
            "BRAINDRIVE_ENABLE_TEST_ENDPOINTS": "true",
        },
    )
    rt.bootstrap()
    return rt


@pytest.fixture
def runtime_ollama_default(tmp_path: Path) -> BrainDriveRuntime:
    library = tmp_path / "library"
    data = tmp_path / "runtime-data"
    rt = BrainDriveRuntime(
        library_root=library,
        data_root=data,
        env={
            "BRAINDRIVE_DEFAULT_PROVIDER": "ollama",
            "BRAINDRIVE_OPENROUTER_API_KEY": "",
            "BRAINDRIVE_OPENROUTER_DEFAULT_MODEL": "anthropic/claude-sonnet-4",
            "BRAINDRIVE_OLLAMA_BASE_URL": "http://localhost:11434/v1",
            "BRAINDRIVE_OLLAMA_DEFAULT_MODEL": "llama3:8b",
            "BRAINDRIVE_OLLAMA_API_KEY": "",
            "BRAINDRIVE_ENABLE_TEST_ENDPOINTS": "true",
        },
    )
    rt.bootstrap()
    return rt


def msg(intent: str, payload: Dict[str, Any], extensions: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "protocol_version": "0.1",
        "message_id": new_uuid(),
        "intent": intent,
        "payload": payload,
    }
    if extensions is not None:
        body["extensions"] = extensions
    return body


@pytest.fixture
def make_message():
    return msg
