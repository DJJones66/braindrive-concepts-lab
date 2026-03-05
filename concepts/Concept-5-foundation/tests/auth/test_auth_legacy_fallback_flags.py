from __future__ import annotations

from services import gateway_adapter_service as gateway


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
