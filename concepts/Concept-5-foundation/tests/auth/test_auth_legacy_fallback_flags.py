from __future__ import annotations

from typing import Dict

from services import gateway_adapter_service as gateway


class _FakeHandler:
    def __init__(self, headers: Dict[str, str] | None = None) -> None:
        self.headers = headers or {}


def _reset_state(monkeypatch) -> None:
    monkeypatch.setattr(gateway, "STATE", gateway._default_state())
    monkeypatch.setattr(gateway, "_persist_state", lambda: None)


def test_legacy_webterm_auth_context_requires_actor_id():
    context, error = gateway.GatewayHandler._legacy_webterm_auth_context("", ["operator"])
    assert context is None
    assert isinstance(error, dict)
    assert error["code"] == "E_AUTH_REQUIRED"


def test_legacy_webterm_auth_context_accepts_explicit_actor_id():
    context, error = gateway.GatewayHandler._legacy_webterm_auth_context("web.user.abc123", ["operator"])
    assert error is None
    assert isinstance(context, dict)
    assert context["actor_id"] == "web.user.abc123"


def test_extract_auth_context_requires_identity_even_with_session_fallback(monkeypatch):
    _reset_state(monkeypatch)
    handler = _FakeHandler()

    auth_context, auth_error, _ = gateway._extract_auth_context(
        handler,
        {},
        allow_session_fallback=True,
        require_identity=True,
    )

    assert auth_context is None
    assert isinstance(auth_error, dict)
    assert auth_error["code"] == "E_AUTH_REQUIRED"
