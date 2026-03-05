from __future__ import annotations

from typing import Dict

from services import gateway_adapter_service as gateway


class _FakeHandler:
    def __init__(self, headers: Dict[str, str] | None = None) -> None:
        self.headers = headers or {}


def _reset_state(monkeypatch) -> None:
    monkeypatch.setattr(gateway, "STATE", gateway._default_state())
    monkeypatch.setattr(gateway, "_persist_state", lambda: None)


def test_extract_auth_context_defaults_to_fail_closed_without_session(monkeypatch):
    _reset_state(monkeypatch)
    handler = _FakeHandler()

    auth_context, auth_error, _ = gateway._extract_auth_context(
        handler,
        {"extensions": {"identity": {"actor_id": "user.contract"}}},
    )

    assert auth_context is None
    assert isinstance(auth_error, dict)
    assert auth_error["code"] == "E_AUTH_REQUIRED"
    assert "valid session is required" in str(auth_error.get("message", ""))


def test_extract_auth_context_uses_session_when_present(monkeypatch):
    _reset_state(monkeypatch)

    created = gateway._register_user("trace-user", "secret", ["operator"], ["chat:write"])
    assert created["ok"] is True
    user = gateway._lookup_user("trace-user")
    assert isinstance(user, dict)
    session = gateway._create_session_for_user(user)

    handler = _FakeHandler(headers={"Authorization": f"Bearer {session['token']}"})
    auth_context, auth_error, extracted = gateway._extract_auth_context(
        handler,
        {},
    )

    assert auth_error is None
    assert extracted == session["token"]
    assert isinstance(auth_context, dict)
    assert isinstance(auth_context.get("auth_session_id"), str)
    assert auth_context.get("auth_session_id", "").strip()


def test_extract_auth_context_rejects_invalid_api_key(monkeypatch):
    _reset_state(monkeypatch)
    monkeypatch.setattr(gateway, "ALLOWED_API_KEYS", {"valid-key"})
    handler = _FakeHandler(headers={"X-API-Key": "bad-key"})

    auth_context, auth_error, _ = gateway._extract_auth_context(
        handler,
        {},
    )

    assert auth_context is None
    assert isinstance(auth_error, dict)
    assert auth_error["code"] == "E_AUTH_FORBIDDEN"
