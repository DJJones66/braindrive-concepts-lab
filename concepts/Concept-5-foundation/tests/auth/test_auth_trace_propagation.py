from __future__ import annotations

from typing import Any, Dict, List

from services import gateway_service as gateway


def test_route_nl_message_propagates_trace_context(monkeypatch):
    captured: List[Dict[str, Any]] = []

    def _fake_post(url: str, payload: Dict[str, Any], timeout_sec: float) -> Dict[str, Any]:
        captured.append(payload)
        return {
            "status": "routed",
            "analysis": {"canonical_intent": "chat.general"},
            "route_message": {"intent": "chat.general"},
            "route_response": {"intent": "chat.response", "payload": {"text": "ok"}},
        }

    monkeypatch.setattr(gateway, "http_post_json", _fake_post)

    auth_context = {
        "actor_id": "user.trace",
        "actor_type": "user",
        "roles": ["operator"],
        "scopes": ["chat:write"],
        "trace_id": "trace-xyz",
        "session_id": "sess-abc",
    }

    response = gateway._route_nl_message(
        {"message": "hello", "context": {}, "metadata": {}},
        auth_context,
        conversation_id="conv-42",
    )

    assert response["ok"] is True
    assert captured
    trace_ext = captured[0]["extensions"]["trace"]
    assert trace_ext["trace_id"] == "trace-xyz"
    assert trace_ext["session_id"] == "sess-abc"
    assert trace_ext["conversation_id"] == "conv-42"


def test_route_bdp_propagates_trace_context(monkeypatch):
    captured: List[Dict[str, Any]] = []

    def _fake_post(url: str, payload: Dict[str, Any], timeout_sec: float) -> Dict[str, Any]:
        captured.append(payload)
        return {"intent": "ok", "payload": {}}

    monkeypatch.setattr(gateway, "http_post_json", _fake_post)

    auth_context = {
        "actor_id": "user.trace",
        "actor_type": "user",
        "roles": ["operator"],
        "scopes": ["chat:write"],
        "trace_id": "trace-bdp",
        "session_id": "sess-bdp",
    }

    result = gateway._route_bdp(
        intent="web.console.session.open",
        payload={"origin": "http://localhost"},
        auth_context=auth_context,
        conversation_id="conv-bdp",
    )

    assert result["intent"] == "ok"
    assert captured
    trace_ext = captured[0]["extensions"]["trace"]
    assert trace_ext == {
        "trace_id": "trace-bdp",
        "session_id": "sess-bdp",
        "conversation_id": "conv-bdp",
    }
