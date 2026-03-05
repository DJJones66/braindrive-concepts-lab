from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

from braindrive_runtime.nodes import web_console as web_console_module
from braindrive_runtime.protocol import new_uuid
from braindrive_runtime.runtime import BrainDriveRuntime


def _msg(intent: str, payload: Dict[str, Any], extensions: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "protocol_version": "0.1",
        "message_id": new_uuid(),
        "intent": intent,
        "payload": payload,
    }
    if extensions is not None:
        body["extensions"] = extensions
    return body


def _runtime(tmp_path: Path, env_overrides: Optional[Dict[str, str]] = None) -> BrainDriveRuntime:
    env = {
        "BRAINDRIVE_DEFAULT_PROVIDER": "openrouter",
        "BRAINDRIVE_OPENROUTER_API_KEY": "test-key",
        "BRAINDRIVE_OPENROUTER_DEFAULT_MODEL": "anthropic/claude-sonnet-4",
        "BRAINDRIVE_OLLAMA_BASE_URL": "http://localhost:11434/v1",
        "BRAINDRIVE_OLLAMA_DEFAULT_MODEL": "llama3:8b",
        "BRAINDRIVE_ENABLE_TEST_ENDPOINTS": "true",
        "WEBTERM_ENABLED": "true",
        "WEBTERM_ALLOWED_ORIGINS": "https://localhost:8443",
        "WEBTERM_SSH_AUTH_MODE": "authorized_keys",
        "WEBTERM_SSH_AUTHORIZED_KEYS_B64": "ZHVtbXk=",
        "WEBTERM_TARGETS": "node-router,node-memory-fs",
        "WEBTERM_SSH_TARGET_DEFAULT": "node-router",
    }
    if env_overrides:
        env.update(env_overrides)
    rt = BrainDriveRuntime(library_root=tmp_path / "library", data_root=tmp_path / "runtime-data", env=env)
    rt.bootstrap()
    return rt


def _identity(actor_id: str = "user.demo") -> Dict[str, Any]:
    return {"identity": {"actor_id": actor_id, "roles": ["operator"]}}


def _web_console_node(runtime):
    item = runtime.nodes.get("node.web.console")
    assert item is not None
    return item.node


def _open(runtime: BrainDriveRuntime, actor_id: str = "user.demo") -> str:
    response = runtime.route(
        _msg(
            "web.console.session.open",
            {"origin": "https://localhost:8443", "target": "node-router"},
            _identity(actor_id),
        )
    )
    assert response["intent"] == "web.console.session.ready"
    return str(response["payload"]["console_session_id"])


def test_web_console_metadata_contract(runtime):
    descriptor = runtime.nodes["node.web.console"].descriptor
    by_name = {item.name: item for item in descriptor.capabilities}
    assert by_name["web.console.session.open"].side_effect_scope == "external"
    assert by_name["web.console.session.event"].side_effect_scope == "external"
    assert by_name["web.console.targets.list"].required_extensions == ["identity"]


def test_web_console_requires_identity_extension(runtime, make_message):
    response = runtime.route(make_message("web.console.session.open", {"origin": "https://localhost:8443"}))
    assert response["intent"] == "error"
    assert response["payload"]["error"]["code"] == "E_REQUIRED_EXTENSION_MISSING"


def test_web_console_open_success_and_targets(tmp_path: Path):
    runtime = _runtime(tmp_path)
    session_id = _open(runtime)
    assert session_id.startswith("sess_")

    targets = runtime.route(_msg("web.console.targets.list", {}, _identity()))
    assert targets["intent"] == "web.console.targets"
    assert "node-router" in targets["payload"]["targets"]

    guides = runtime.route(_msg("web.console.guides.list", {}, _identity()))
    assert guides["intent"] == "web.console.guides"
    assert len(guides["payload"]["guides"]) >= 1


def test_web_console_origin_deny(tmp_path: Path):
    runtime = _runtime(tmp_path, {"WEBTERM_ALLOWED_ORIGINS": "https://allowed.example"})
    denied = runtime.route(
        _msg(
            "web.console.session.open",
            {"origin": "https://localhost:8443"},
            _identity(),
        )
    )
    assert denied["intent"] == "error"
    assert denied["payload"]["error"]["code"] == "E_WEBTERM_ORIGIN_DENIED"


def test_web_console_session_limit(tmp_path: Path):
    runtime = _runtime(tmp_path, {"WEBTERM_MAX_CONCURRENT_SESSIONS_PER_USER": "1"})
    _open(runtime, actor_id="user.one")
    second = runtime.route(
        _msg(
            "web.console.session.open",
            {"origin": "https://localhost:8443"},
            _identity("user.one"),
        )
    )
    assert second["intent"] == "error"
    assert second["payload"]["error"]["code"] == "E_WEBTERM_POLICY_DENIED"


def test_web_console_open_prunes_expired_sessions(tmp_path: Path):
    runtime = _runtime(
        tmp_path,
        {
            "WEBTERM_MAX_CONCURRENT_SESSIONS_PER_USER": "1",
            "WEBTERM_SESSION_IDLE_TIMEOUT_SEC": "30",
        },
    )
    first_session_id = _open(runtime, actor_id="user.one")
    node = _web_console_node(runtime)
    session = node._get_session(first_session_id)  # noqa: SLF001
    assert session is not None
    session["last_activity_epoch"] = time.time() - 1000.0
    node._save()  # noqa: SLF001

    reopened = runtime.route(
        _msg(
            "web.console.session.open",
            {"origin": "https://localhost:8443"},
            _identity("user.one"),
        )
    )
    assert reopened["intent"] == "web.console.session.ready"
    assert str(reopened["payload"]["console_session_id"]) != first_session_id


def test_web_console_slash_commands_toggle_raw_mode(tmp_path: Path):
    runtime = _runtime(tmp_path)
    session_id = _open(runtime)

    response = runtime.route(
        _msg(
            "web.console.session.event",
            {
                "console_session_id": session_id,
                "event": "terminal.input",
                "payload": {"data": "/raw on"},
            },
            _identity(),
        )
    )

    assert response["intent"] == "web.console.session.events"
    node = _web_console_node(runtime)
    session = node._get_session(session_id)  # noqa: SLF001
    assert session is not None
    assert session.get("raw_mode") is True


def test_web_console_plain_interview_answer_routes_without_context_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    runtime = _runtime(tmp_path)
    created = runtime.route_nl("create folder dimes", confirm=True)
    assert created["status"] == "routed"
    assert created["route_response"]["intent"] == "folder.created"

    def _fake_http_post_json(url: str, payload: Dict[str, Any], timeout_sec: float) -> Dict[str, Any]:
        assert url.endswith("/api/v1/messages")
        message = str(payload.get("message", ""))
        context = payload.get("context", {})
        extensions = payload.get("extensions", {})
        routed = runtime.intent_router.route(
            message,
            context=context if isinstance(context, dict) else None,
            confirm=bool(payload.get("confirm", False)),
            request_extensions=extensions if isinstance(extensions, dict) else None,
        )
        return {
            "ok": True,
            "conversation_id": "conv-test",
            "status": routed.get("status", ""),
            "analysis": routed.get("analysis", {}),
            "route_message": routed.get("route_message", {}),
            "route_response": routed.get("route_response", {}),
        }

    monkeypatch.setattr(web_console_module, "http_post_json", _fake_http_post_json)

    session_id = _open(runtime)

    switched = runtime.route(
        _msg(
            "web.console.session.event",
            {
                "console_session_id": session_id,
                "event": "terminal.input",
                "payload": {"data": "switch folder to dimes"},
            },
            _identity(),
        )
    )
    assert switched["intent"] == "web.console.session.events"

    started = runtime.route(
        _msg(
            "web.console.session.event",
            {
                "console_session_id": session_id,
                "event": "terminal.input",
                "payload": {"data": "start interview"},
            },
            _identity(),
        )
    )
    assert started["intent"] == "web.console.session.events"

    answer_text = "I need OCR-assisted invoice reconciliation."
    continued = runtime.route(
        _msg(
            "web.console.session.event",
            {
                "console_session_id": session_id,
                "event": "terminal.input",
                "payload": {"data": answer_text},
            },
            _identity(),
        )
    )
    assert continued["intent"] == "web.console.session.events"

    interview = runtime.route(_msg("session.interview.get", {"folder": "dimes"}))
    assert interview["intent"] == "session.interview"
    interview_payload = interview["payload"]["interview"]
    answers = interview_payload.get("answers", [])
    assert isinstance(answers, list)
    assert answers
    assert answers[-1]["answer"] == answer_text


def test_web_console_mutation_requires_and_accepts_confirmation(tmp_path: Path):
    runtime = _runtime(tmp_path)
    session_id = _open(runtime)

    pending = runtime.route(
        _msg(
            "web.console.session.event",
            {
                "console_session_id": session_id,
                "event": "terminal.input",
                "payload": {"data": "git commit -m 'msg'"},
            },
            _identity(),
        )
    )
    assert pending["intent"] == "web.console.session.approval_required"
    request_id = pending["payload"]["approval_request_id"]

    approved = runtime.route(
        _msg(
            "web.console.session.event",
            {
                "console_session_id": session_id,
                "event": "terminal.input",
                "payload": {"data": "git commit -m 'msg'"},
            },
            {
                **_identity(),
                "confirmation": {
                    "required": True,
                    "status": "approved",
                    "request_id": request_id,
                },
            },
        )
    )
    assert approved["intent"] == "web.console.session.events"
    assert approved["payload"]["classification"] == "mutate"
    assert approved["payload"]["policy_decision"] == "approved"


def test_web_console_intent_fallback_is_opt_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    runtime = _runtime(tmp_path, {"WEBTERM_ALLOW_INTENT_FALLBACK": "true"})
    session_id = _open(runtime)

    def _fake_http_post_json(url: str, payload: Dict[str, Any], timeout_sec: float) -> Dict[str, Any]:
        if url.endswith("/api/v1/messages"):
            raise RuntimeError("gateway unavailable")
        assert url.endswith("/intent/route")
        return runtime.intent_router.route(
            str(payload.get("message", "")),
            context=payload.get("context", {}) if isinstance(payload.get("context"), dict) else None,
            confirm=bool(payload.get("confirm", False)),
        )

    monkeypatch.setattr(web_console_module, "http_post_json", _fake_http_post_json)

    response = runtime.route(
        _msg(
            "web.console.session.event",
            {
                "console_session_id": session_id,
                "event": "terminal.input",
                "payload": {"data": "tell me a joke"},
            },
            _identity(),
        )
    )
    assert response["intent"] == "web.console.session.events"


def test_web_console_session_expiration(tmp_path: Path):
    runtime = _runtime(tmp_path, {"WEBTERM_SESSION_IDLE_TIMEOUT_SEC": "30"})
    session_id = _open(runtime)
    node = _web_console_node(runtime)
    session = node._get_session(session_id)  # noqa: SLF001
    assert session is not None
    session["last_activity_epoch"] = time.time() - 1000.0
    node._save()  # noqa: SLF001

    expired = runtime.route(
        _msg(
            "web.console.session.event",
            {"console_session_id": session_id, "event": "session.ping", "payload": {}},
            _identity(),
        )
    )
    assert expired["intent"] == "error"
    assert expired["payload"]["error"]["code"] == "E_WEBTERM_SESSION_EXPIRED"


def test_web_console_inline_private_key_rejected_in_production(tmp_path: Path):
    with pytest.raises(ValueError):
        _runtime(
            tmp_path,
            {
                "BRAINDRIVE_ENV": "production",
                "WEBTERM_SSH_AUTH_MODE": "static_client_key",
                "WEBTERM_SSH_CLIENT_KEY_B64": "cHJpdmF0ZS1rZXk=",
                "WEBTERM_SSH_CLIENT_KEY_FILE": "",
            },
        )
