from __future__ import annotations

from typing import Any, Dict, List

from services import gateway_adapter_service as gateway
from services import gateway_core_service as gateway_core


def _reset_state(monkeypatch) -> None:
    monkeypatch.setattr(gateway, "STATE", gateway._default_state())
    monkeypatch.setattr(gateway, "_persist_state", lambda: None)


def test_core_request_schema_rejects_missing_actor_id():
    errors = gateway_core.validate_core_request_envelope(
        {
            "request_id": "req_1",
            "auth_context": {"roles": ["operator"], "scopes": ["chat:write"]},
        }
    )
    assert errors
    assert any("auth_context.actor_id is required" in item for item in errors)


def test_core_request_schema_strict_mode_rejects_unknown_fields():
    errors = gateway_core.validate_core_request_envelope(
        {
            "request_id": "req_1",
            "auth_context": {"actor_id": "user.demo", "roles": ["operator"], "scopes": []},
            "unexpected": True,
        },
        strict=True,
    )
    assert errors
    assert any("unknown fields in strict mode" in item for item in errors)


def test_adapter_builds_core_contract_request_envelope():
    request = gateway._core_contract_request(
        auth_context={
            "actor_id": "user.demo",
            "actor_type": "user",
            "roles": ["operator"],
            "scopes": ["chat:write"],
            "trace_id": "trace-1",
            "auth_session_id": "sess-1",
        },
        conversation_id="conv_demo",
        message="hello",
        context={"channel": "test"},
        metadata={"client": {"client_id": "pytest"}},
        confirm=False,
    )

    assert request["request_id"].startswith("req_")
    assert request["conversation_id"] == "conv_demo"
    assert request["core_contract_version"] == gateway_core.CORE_CONTRACT_VERSION
    assert request["adapter_contract_version"] == "v1"
    assert gateway_core.validate_core_request_envelope(request) == []


def test_core_message_contract_roundtrip_emits_canonical_events(monkeypatch):
    _reset_state(monkeypatch)
    captured: List[Dict[str, Any]] = []

    def _fake_post(url: str, payload: Dict[str, Any], timeout_sec: float) -> Dict[str, Any]:
        captured.append(payload)
        assert url.endswith("/intent/route")
        return {
            "status": "routed",
            "analysis": {"canonical_intent": "chat.general"},
            "route_message": {"intent": "chat.general"},
            "route_response": {"intent": "chat.response", "payload": {"text": "hello back"}},
        }

    auth_context = {
        "actor_id": "user.demo",
        "actor_type": "user",
        "roles": ["operator"],
        "scopes": ["chat:write"],
        "trace_id": "trace-a",
        "auth_session_id": "sess-a",
    }
    request = gateway._core_contract_request(
        auth_context=auth_context,
        conversation_id="conv_1",
        message="hello",
        context={},
        metadata={"channel": "test"},
    )
    result = gateway_core.core_v1_messages(
        state=gateway.STATE,
        persist_state=gateway._persist_state,
        append_log=lambda _name, _payload: None,
        request=request,
        intent_router_base_url="http://intent-router",
        http_timeout_sec=5.0,
        post_json=_fake_post,
    )

    assert result["ok"] is True
    assert captured
    assert gateway_core.validate_core_response_envelope(result) == []

    events = gateway_core.core_v1_stream_events(
        state=gateway.STATE,
        persist_state=gateway._persist_state,
        conversation_id="conv_1",
    )
    assert events
    names = [item["event"] for item in events]
    assert "metadata" in names
    assert "delta" in names
    assert "complete" in names
    for event in events:
        assert gateway_core.validate_stream_event_envelope(event) == []
