from __future__ import annotations

from typing import Any, Dict, List

from services import gateway_adapter_service as gateway


class _FakeHandler:
    def __init__(self, headers: Dict[str, str] | None = None) -> None:
        self.headers = headers or {}


def _reset_state(monkeypatch) -> None:
    monkeypatch.setattr(gateway, "STATE", gateway._default_state())
    monkeypatch.setattr(gateway, "_persist_state", lambda: None)


def test_session_auth_context_identity_is_forwarded_to_route_payload(monkeypatch):
    _reset_state(monkeypatch)

    created = gateway._register_user("equiv", "secret", ["operator", "reviewer"], ["chat:write"])
    assert created["ok"] is True
    user = gateway._lookup_user("equiv")
    assert isinstance(user, dict)
    session = gateway._create_session_for_user(user)

    handler = _FakeHandler(headers={"Authorization": f"Bearer {session['token']}"})
    session_context, session_error, _ = gateway._extract_auth_context(
        handler,
        {},
    )
    assert session_error is None
    assert isinstance(session_context, dict)

    captured_payloads: List[Dict[str, Any]] = []

    def _fake_post(url: str, payload: Dict[str, Any], timeout_sec: float) -> Dict[str, Any]:
        captured_payloads.append(payload)
        return {
            "status": "routed",
            "analysis": {"canonical_intent": "chat.general"},
            "route_message": {"intent": "chat.general"},
            "route_response": {"intent": "chat.response", "payload": {"text": "ok"}},
        }

    monkeypatch.setattr(gateway, "http_post_json", _fake_post)

    gateway._route_nl_message({"message": "hello", "context": {}, "metadata": {}}, session_context, conversation_id="conv-a")

    assert len(captured_payloads) == 1
    forwarded_identity = captured_payloads[0]["extensions"]["identity"]
    assert forwarded_identity["actor_id"] == session_context["actor_id"]
    assert forwarded_identity["roles"] == session_context["roles"]
    assert forwarded_identity["actor_type"] == session_context["actor_type"]
    assert forwarded_identity["scopes"] == session_context["scopes"]
