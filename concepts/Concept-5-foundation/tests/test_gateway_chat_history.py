from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from services import gateway_core_service as gateway_core


def _auth_context() -> Dict[str, Any]:
    return {
        "actor_id": "user.demo",
        "actor_type": "user",
        "roles": ["operator"],
        "scopes": ["chat:write"],
        "trace_id": "trace-1",
        "auth_session_id": "sess-auth-1",
    }


def _fake_router_response(reply_text: str) -> Dict[str, Any]:
    return {
        "status": "routed",
        "analysis": {"canonical_intent": "chat.general"},
        "route_message": {"intent": "chat.general"},
        "route_response": {"intent": "chat.response", "payload": {"text": reply_text}},
    }


def test_route_nl_message_writes_chat_jsonl_and_sidecar(tmp_path: Path):
    state = gateway_core.default_core_state()
    library_root = tmp_path / "library"
    captured: List[Dict[str, Any]] = []

    def _fake_post(url: str, payload: Dict[str, Any], timeout_sec: float) -> Dict[str, Any]:
        captured.append(payload)
        return _fake_router_response("hello back")

    result = gateway_core.route_nl_message(
        state=state,
        persist_state=lambda: None,
        append_log=lambda _name, _payload: None,
        intent_router_base_url="http://intent-router",
        http_timeout_sec=5.0,
        body={"message": "hello", "context": {}, "metadata": {"channel": "api"}},
        auth_context=_auth_context(),
        conversation_id="conv_1",
        library_root=str(library_root),
        provider_context_enabled=True,
        provider_context_max_turns=12,
        provider_context_max_chars=12000,
        chat_sidecar_enabled=True,
        post_json=_fake_post,
    )

    assert result["ok"] is True
    assert captured

    chat_file = library_root / "chats" / "conv_1.jsonl"
    sidecar_file = library_root / "chats" / "conv_1.meta.json"
    assert chat_file.exists()
    assert sidecar_file.exists()

    records = [json.loads(line) for line in chat_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(records) == 1
    first = records[0]
    assert first["conversation_id"] == "conv_1"
    assert first["input"]["text"] == "hello"
    assert first["output"]["text"] == "hello back"
    assert first["trace"]["auth_session_id"] == "sess-auth-1"

    sidecar = json.loads(sidecar_file.read_text(encoding="utf-8"))
    assert sidecar["conversation_id"] == "conv_1"
    assert sidecar["record_count"] == 1


def test_route_nl_message_reuses_bounded_history_for_provider_context(tmp_path: Path):
    state = gateway_core.default_core_state()
    library_root = tmp_path / "library"
    captured: List[Dict[str, Any]] = []

    def _fake_post(url: str, payload: Dict[str, Any], timeout_sec: float) -> Dict[str, Any]:
        captured.append(payload)
        if payload["message"] == "first":
            return _fake_router_response("first-reply")
        return _fake_router_response("second-reply")

    gateway_core.route_nl_message(
        state=state,
        persist_state=lambda: None,
        append_log=lambda _name, _payload: None,
        intent_router_base_url="http://intent-router",
        http_timeout_sec=5.0,
        body={"message": "first", "context": {}, "metadata": {"channel": "api"}},
        auth_context=_auth_context(),
        conversation_id="conv_history",
        library_root=str(library_root),
        provider_context_enabled=True,
        provider_context_max_turns=6,
        provider_context_max_chars=1000,
        chat_sidecar_enabled=True,
        post_json=_fake_post,
    )
    gateway_core.route_nl_message(
        state=state,
        persist_state=lambda: None,
        append_log=lambda _name, _payload: None,
        intent_router_base_url="http://intent-router",
        http_timeout_sec=5.0,
        body={"message": "second", "context": {}, "metadata": {"channel": "api"}},
        auth_context=_auth_context(),
        conversation_id="conv_history",
        library_root=str(library_root),
        provider_context_enabled=True,
        provider_context_max_turns=6,
        provider_context_max_chars=1000,
        chat_sidecar_enabled=True,
        post_json=_fake_post,
    )

    assert len(captured) == 2
    second_context = captured[1].get("context", {})
    history = second_context.get("provider_history_messages", [])
    assert history == [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "first-reply"},
    ]


def test_route_nl_message_logs_chat_persistence_failure(monkeypatch, tmp_path: Path):
    state = gateway_core.default_core_state()
    library_root = tmp_path / "library"
    logs: List[Dict[str, Any]] = []

    def _fake_post(_url: str, _payload: Dict[str, Any], timeout_sec: float) -> Dict[str, Any]:
        _ = timeout_sec
        return _fake_router_response("hello back")

    def _append_log(name: str, payload: Dict[str, Any]) -> None:
        logs.append({"name": name, "payload": payload})

    def _raise_append(*, library_root: str, conversation_id: str, record: Dict[str, Any], write_sidecar: bool) -> None:
        raise PermissionError("read-only file system")

    monkeypatch.setattr(gateway_core, "_append_chat_record", _raise_append)

    result = gateway_core.route_nl_message(
        state=state,
        persist_state=lambda: None,
        append_log=_append_log,
        intent_router_base_url="http://intent-router",
        http_timeout_sec=5.0,
        body={"message": "hello", "context": {}, "metadata": {"channel": "api"}},
        auth_context=_auth_context(),
        conversation_id="conv_fail",
        library_root=str(library_root),
        provider_context_enabled=True,
        provider_context_max_turns=12,
        provider_context_max_chars=12000,
        chat_sidecar_enabled=True,
        post_json=_fake_post,
    )

    assert result["ok"] is True
    persist_logs = [item for item in logs if item.get("name") == "gateway_chat_persistence"]
    assert persist_logs
    payload = persist_logs[-1]["payload"]
    assert payload["conversation_id"] == "conv_fail"
    assert "read-only" in str(payload.get("error", "")).lower()
