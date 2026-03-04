from __future__ import annotations

from typing import Any, Dict, List

from services import gateway_adapter_service as gateway


class _FakeHandler:
    def __init__(self, headers: Dict[str, str] | None = None) -> None:
        self.headers = headers or {}


def _reset_state(monkeypatch) -> None:
    monkeypatch.setattr(gateway, "STATE", gateway._default_state())
    monkeypatch.setattr(gateway, "_persist_state", lambda: None)


def test_session_and_identity_modes_produce_equivalent_identity_payload(monkeypatch):
    _reset_state(monkeypatch)

    created = gateway._register_user("equiv", "secret", ["operator", "reviewer"], ["chat:write"])
    assert created["ok"] is True
    user = gateway._lookup_user("equiv")
    assert isinstance(user, dict)
    session = gateway._create_session_for_user(user)

    session_handler = _FakeHandler(headers={"Authorization": f"Bearer {session['token']}"})
    session_context, session_error, _ = gateway._extract_auth_context(
        session_handler,
        {},
        allow_session_fallback=False,
        require_identity=True,
    )
    assert session_error is None
    assert isinstance(session_context, dict)

    identity_handler = _FakeHandler()
    identity_context, identity_error, _ = gateway._extract_auth_context(
        identity_handler,
        {
            "extensions": {
                "identity": {
                    "actor_id": session_context["actor_id"],
                    "roles": session_context["roles"],
                    "actor_type": session_context["actor_type"],
                    "scopes": session_context["scopes"],
                }
            }
        },
        allow_session_fallback=False,
        require_identity=True,
    )
    assert identity_error is None
    assert isinstance(identity_context, dict)

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
    gateway._route_nl_message({"message": "hello", "context": {}, "metadata": {}}, identity_context, conversation_id="conv-b")

    assert len(captured_payloads) == 2
    first_identity = captured_payloads[0]["extensions"]["identity"]
    second_identity = captured_payloads[1]["extensions"]["identity"]
    assert first_identity == second_identity
