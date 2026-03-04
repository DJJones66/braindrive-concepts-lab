from __future__ import annotations

from typing import Any, Dict

from services import gateway_adapter_service as gateway


class _FakeHandler:
    def __init__(self, headers: Dict[str, str] | None = None) -> None:
        self.headers = headers or {}


def _reset_state(monkeypatch) -> None:
    monkeypatch.setattr(gateway, "STATE", gateway._default_state())
    monkeypatch.setattr(gateway, "_persist_state", lambda: None)


def test_extract_auth_context_accepts_valid_identity(monkeypatch):
    _reset_state(monkeypatch)
    handler = _FakeHandler()

    auth_context, auth_error, token = gateway._extract_auth_context(
        handler,
        {
            "extensions": {
                "identity": {
                    "actor_id": "user.contract",
                    "roles": ["operator"],
                    "actor_type": "user",
                    "scopes": ["chat:write"],
                }
            }
        },
        allow_session_fallback=False,
        require_identity=True,
    )

    assert auth_error is None
    assert token == ""
    assert isinstance(auth_context, dict)
    assert auth_context["actor_id"] == "user.contract"
    assert auth_context["roles"] == ["operator"]
    assert auth_context["scopes"] == ["chat:write"]


def test_extract_auth_context_rejects_missing_actor_id(monkeypatch):
    _reset_state(monkeypatch)
    handler = _FakeHandler()

    auth_context, auth_error, _ = gateway._extract_auth_context(
        handler,
        {"extensions": {"identity": {"roles": ["operator"]}}},
        allow_session_fallback=False,
        require_identity=True,
    )

    assert auth_context is None
    assert auth_error is not None
    assert auth_error["code"] == "E_AUTH_REQUIRED"


def test_extract_auth_context_normalizes_roles_and_scopes(monkeypatch):
    _reset_state(monkeypatch)
    handler = _FakeHandler()

    auth_context, auth_error, _ = gateway._extract_auth_context(
        handler,
        {
            "extensions": {
                "identity": {
                    "actor_id": "user.normalize",
                    "roles": " operator, admin ",
                    "scopes": "chat:write, git:mutate ",
                }
            }
        },
        allow_session_fallback=False,
        require_identity=True,
    )

    assert auth_error is None
    assert isinstance(auth_context, dict)
    assert auth_context["roles"] == ["operator", "admin"]
    assert auth_context["scopes"] == ["chat:write", "git:mutate"]


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
        allow_session_fallback=False,
        require_identity=True,
    )

    assert auth_error is None
    assert extracted == session["token"]
    assert isinstance(auth_context, dict)
    assert isinstance(auth_context.get("session_id"), str)
    assert auth_context.get("session_id", "").strip()
