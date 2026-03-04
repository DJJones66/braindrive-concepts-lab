from __future__ import annotations

from typing import Any, Dict

from services import gateway_service as gateway


class _FakeHandler:
    def __init__(self, headers: Dict[str, str] | None = None) -> None:
        self.headers = headers or {}


def _reset_state(monkeypatch) -> None:
    monkeypatch.setattr(gateway, "STATE", gateway._default_state())
    monkeypatch.setattr(gateway, "_persist_state", lambda: None)


def test_legacy_gateway_routes_default_disabled():
    assert gateway.ENABLE_LEGACY_GATEWAY_ROUTES is False


def test_register_user_and_detect_duplicate(monkeypatch):
    _reset_state(monkeypatch)

    created = gateway._register_user("alice", "secret", ["operator"], ["chat:write"])
    assert created["ok"] is True
    assert created["user"]["username"] == "alice"

    duplicate = gateway._register_user("alice", "secret", ["operator"], ["chat:write"])
    assert duplicate["ok"] is False
    assert duplicate["error"]["code"] == "E_USER_EXISTS"


def test_extract_auth_context_uses_session_token(monkeypatch):
    _reset_state(monkeypatch)

    user_result = gateway._register_user("bob", "secret", ["operator"], ["chat:write"])
    assert user_result["ok"] is True
    user = gateway._lookup_user("bob")
    assert isinstance(user, dict)

    session = gateway._create_session_for_user(user)
    token = str(session["token"])

    handler = _FakeHandler(headers={"Authorization": f"Bearer {token}"})
    auth_context, auth_error, extracted = gateway._extract_auth_context(handler, {}, allow_session_fallback=False)

    assert auth_error is None
    assert auth_context is not None
    assert extracted == token
    assert auth_context["actor_id"].startswith("user.")


def test_route_nl_message_enqueues_stream_events(monkeypatch):
    _reset_state(monkeypatch)

    def _fake_post(url: str, payload: Dict[str, Any], timeout_sec: float) -> Dict[str, Any]:
        assert url.endswith("/intent/route")
        assert payload["message"] == "hello"
        return {
            "status": "routed",
            "analysis": {"canonical_intent": "chat.general"},
            "route_message": {"intent": "chat.general"},
            "route_response": {
                "intent": "chat.response",
                "payload": {"text": "hi there"},
            },
        }

    monkeypatch.setattr(gateway, "http_post_json", _fake_post)

    auth_context = {
        "actor_id": "user.demo",
        "actor_type": "user",
        "roles": ["operator"],
        "scopes": [],
        "trace_id": "trace-1",
        "session_id": "",
    }
    result = gateway._route_nl_message(
        {
            "message": "hello",
            "context": {},
            "metadata": {"channel": "test"},
        },
        auth_context,
        conversation_id="conv_1",
    )

    assert result["ok"] is True
    events = gateway._pop_stream_events("conv_1")
    names = [item["event"] for item in events]
    assert "metadata" in names
    assert "delta" in names
    assert "complete" in names


def test_console_input_approval_event_is_queued(monkeypatch):
    _reset_state(monkeypatch)

    monkeypatch.setitem(
        gateway.STATE["console_sessions"],
        "sess_123",
        {
            "console_session_id": "sess_123",
            "conversation_id": "conv_123",
            "actor_id": "user.demo",
            "target": "node-router",
            "opened_at": "now",
        },
    )

    def _fake_route_bdp(**kwargs):
        return {
            "intent": "web.console.session.approval_required",
            "payload": {
                "session_id": "sess_123",
                "approval_request_id": "appr_1",
                "command": "git commit -m 'x'",
            },
        }

    monkeypatch.setattr(gateway, "_route_bdp", _fake_route_bdp)

    auth_context = {
        "actor_id": "user.demo",
        "actor_type": "user",
        "roles": ["operator"],
        "scopes": [],
        "trace_id": "trace-1",
        "session_id": "",
    }

    result = gateway._handle_console_input(
        {
            "console_session_id": "sess_123",
            "text": "git commit -m 'x'",
        },
        auth_context,
        "conv_123",
    )
    assert result["ok"] is True

    events = gateway._pop_stream_events("conv_123")
    assert any(item["event"] == "approval_required" for item in events)
