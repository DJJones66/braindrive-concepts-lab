#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlsplit

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from braindrive_runtime.persistence import Persistence
from braindrive_runtime.protocol import http_post_json, new_uuid, now_iso
from services import gateway_core_service as gateway_core
from services.web_terminal_ui import WEB_TERMINAL_HTML


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, str(default)).strip().lower()
    return raw in {"1", "true", "yes", "on"}


PORT = int(os.getenv("GATEWAY_PORT", "8090"))
INTENT_ROUTER_BASE_URL = os.getenv("GATEWAY_INTENT_ROUTER_BASE_URL", "http://intent-router-natural-language:8081").rstrip("/")
ROUTER_BASE_URL = os.getenv("GATEWAY_ROUTER_BASE_URL", "http://node-router:8080").rstrip("/")
DEFAULT_RUNTIME_DIR = PROJECT_ROOT / "data" / "runtime" / "gateway"
RUNTIME_DIR = Path(os.getenv("BRAINDRIVE_RUNTIME_DIR", str(DEFAULT_RUNTIME_DIR)))
DEFAULT_LIBRARY_ROOT = PROJECT_ROOT / "data" / "library"
LIBRARY_ROOT = Path(os.getenv("BRAINDRIVE_LIBRARY_ROOT", str(DEFAULT_LIBRARY_ROOT)))
HTTP_TIMEOUT_SEC = float(os.getenv("GATEWAY_HTTP_TIMEOUT_SEC", "70.0"))
AUTH_REQUIRED = _env_bool("GATEWAY_AUTH_REQUIRED", True)
ENFORCE_SESSION = _env_bool("GATEWAY_ENFORCE_SESSION", False)
ENABLE_LEGACY_GATEWAY_ROUTES = _env_bool("GATEWAY_ENABLE_LEGACY_COMPAT_ROUTES", False)
CORE_CONTRACT_STRICT = _env_bool("GATEWAY_CORE_CONTRACT_STRICT", False)
PROVIDER_CONTEXT_ENABLED = _env_bool("GATEWAY_PROVIDER_CONTEXT_ENABLED", True)
PROVIDER_CONTEXT_MAX_TURNS = int(os.getenv("GATEWAY_PROVIDER_CONTEXT_MAX_TURNS", "12"))
PROVIDER_CONTEXT_MAX_CHARS = int(os.getenv("GATEWAY_PROVIDER_CONTEXT_MAX_CHARS", "12000"))
CHAT_SIDECAR_ENABLED = _env_bool("GATEWAY_CHAT_SIDECAR_ENABLED", True)
DEFAULT_ACTOR_TYPE = os.getenv("GATEWAY_DEFAULT_ACTOR_TYPE", "user").strip() or "user"
SESSION_TTL_SEC = int(os.getenv("GATEWAY_SESSION_TTL_SEC", "43200"))
REFRESH_TTL_SEC = int(os.getenv("GATEWAY_REFRESH_TTL_SEC", "604800"))
AUTH_SALT = os.getenv("GATEWAY_AUTH_SALT", "braindrive-gateway-salt")
ALLOWED_API_KEYS = {item.strip() for item in os.getenv("GATEWAY_API_KEYS", "").split(",") if item.strip()}

RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
LIBRARY_ROOT.mkdir(parents=True, exist_ok=True)
PERSISTENCE = Persistence(RUNTIME_DIR)


def _default_state() -> Dict[str, Any]:
    state = {
        "users": {},
        "sessions": {},
        "refresh_index": {},
    }
    state.update(gateway_core.default_core_state())
    return state


STATE = PERSISTENCE.load_state("gateway_state", _default_state())
if not isinstance(STATE, dict):
    STATE = _default_state()
for key, fallback in _default_state().items():
    if not isinstance(STATE.get(key), type(fallback)):
        STATE[key] = fallback


def _persist_state() -> None:
    PERSISTENCE.save_state("gateway_state", STATE)


def _path_only(raw_path: str) -> str:
    return urlsplit(raw_path).path


def _query_values(raw_path: str) -> Dict[str, str]:
    values = parse_qs(urlsplit(raw_path).query, keep_blank_values=True)
    out: Dict[str, str] = {}
    for key, items in values.items():
        if not items:
            continue
        out[key] = str(items[0])
    return out


def _normalize_roles(raw: Any) -> List[str]:
    if isinstance(raw, list):
        values = [str(item).strip() for item in raw if str(item).strip()]
        return values or ["operator"]
    text = str(raw).strip()
    if not text:
        return ["operator"]
    return [item.strip() for item in text.split(",") if item.strip()] or ["operator"]


def _normalize_scopes(raw: Any) -> List[str]:
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    text = str(raw).strip()
    if not text:
        return []
    return [item.strip() for item in text.split(",") if item.strip()]


def _identity_from_body(body: Dict[str, Any]) -> Dict[str, Any]:
    extensions = body.get("extensions", {})
    if isinstance(extensions, dict):
        identity = extensions.get("identity", {})
        if isinstance(identity, dict):
            return identity

    identity = body.get("identity", {})
    if isinstance(identity, dict):
        return identity

    return {
        "actor_id": body.get("actor_id", ""),
        "roles": body.get("roles", []),
        "actor_type": body.get("actor_type", DEFAULT_ACTOR_TYPE),
        "scopes": body.get("scopes", []),
    }


def _hash_password(password: str) -> str:
    return hashlib.sha256(f"{AUTH_SALT}:{password}".encode("utf-8")).hexdigest()


def _parse_cookies(raw_cookie: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for part in str(raw_cookie).split(";"):
        token = part.strip()
        if not token or "=" not in token:
            continue
        key, value = token.split("=", 1)
        out[key.strip()] = value.strip()
    return out


def _session_token_from_request(handler: BaseHTTPRequestHandler) -> str:
    auth = str(handler.headers.get("Authorization", "")).strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    cookies = _parse_cookies(str(handler.headers.get("Cookie", "")))
    return str(cookies.get("bd_session", "")).strip()


def _extract_api_key(handler: BaseHTTPRequestHandler) -> str:
    return str(handler.headers.get("X-API-Key", "")).strip()


def _cleanup_expired_sessions() -> None:
    sessions = STATE.setdefault("sessions", {})
    refresh_index = STATE.setdefault("refresh_index", {})
    now = int(time.time())
    expired: List[str] = []

    for token, session in list(sessions.items()):
        if not isinstance(session, dict):
            expired.append(str(token))
            continue
        expires_at = int(session.get("expires_at", 0))
        refresh_expires_at = int(session.get("refresh_expires_at", 0))
        if now >= refresh_expires_at or now >= expires_at:
            expired.append(str(token))

    if not expired:
        return

    for token in expired:
        session = sessions.pop(token, {})
        if isinstance(session, dict):
            refresh_token = str(session.get("refresh_token", "")).strip()
            if refresh_token:
                refresh_index.pop(refresh_token, None)

    _persist_state()


def _auth_error(code: str, message: str) -> Dict[str, Any]:
    return {"code": code, "message": message}


def _build_auth_context_from_session(session: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "actor_id": str(session.get("actor_id", "")),
        "actor_type": str(session.get("actor_type", DEFAULT_ACTOR_TYPE)),
        "roles": _normalize_roles(session.get("roles", [])),
        "scopes": _normalize_scopes(session.get("scopes", [])),
        "trace_id": str(new_uuid()),
        "auth_session_id": str(session.get("auth_session_id", "")),
    }


def _build_auth_context_from_identity(handler: BaseHTTPRequestHandler, body: Dict[str, Any]) -> Dict[str, Any]:
    identity = _identity_from_body(body)
    actor_id = str(identity.get("actor_id", "")).strip() or str(handler.headers.get("X-Actor-Id", "")).strip()
    roles = _normalize_roles(identity.get("roles", handler.headers.get("X-Actor-Roles", "operator")))
    actor_type = str(identity.get("actor_type", DEFAULT_ACTOR_TYPE)).strip() or DEFAULT_ACTOR_TYPE
    scopes = _normalize_scopes(identity.get("scopes", []))

    return {
        "actor_id": actor_id,
        "actor_type": actor_type,
        "roles": roles,
        "scopes": scopes,
        "trace_id": str(new_uuid()),
        "auth_session_id": "",
    }


def _extract_auth_context(
    handler: BaseHTTPRequestHandler,
    body: Dict[str, Any],
    *,
    allow_session_fallback: bool,
    require_identity: bool = True,
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], str]:
    api_key = _extract_api_key(handler)
    if ALLOWED_API_KEYS and api_key not in ALLOWED_API_KEYS:
        return None, _auth_error("E_AUTH_FORBIDDEN", "API key is invalid or missing"), ""

    _cleanup_expired_sessions()

    token = _session_token_from_request(handler)
    session = STATE.setdefault("sessions", {}).get(token)
    if isinstance(session, dict):
        return _build_auth_context_from_session(session), None, token

    if ENFORCE_SESSION and not allow_session_fallback:
        return None, _auth_error("E_AUTH_REQUIRED", "valid session is required"), ""

    context = _build_auth_context_from_identity(handler, body)
    actor_id = str(context.get("actor_id", "")).strip()
    if require_identity and not actor_id:
        return None, _auth_error("E_AUTH_REQUIRED", "actor_id is required"), ""
    if AUTH_REQUIRED and not actor_id and not allow_session_fallback:
        return None, _auth_error("E_AUTH_REQUIRED", "actor_id is required"), ""

    return context, None, ""


def _lookup_user(username: str) -> Optional[Dict[str, Any]]:
    users = STATE.setdefault("users", {})
    item = users.get(username)
    return item if isinstance(item, dict) else None


def _register_user(username: str, password: str, roles: List[str], scopes: List[str]) -> Dict[str, Any]:
    users = STATE.setdefault("users", {})
    if username in users:
        return {"ok": False, "error": _auth_error("E_USER_EXISTS", "username already exists")}

    user_id = f"usr_{new_uuid()}"
    now = int(time.time())
    users[username] = {
        "user_id": user_id,
        "username": username,
        "password_hash": _hash_password(password),
        "roles": roles,
        "scopes": scopes,
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    _persist_state()

    return {
        "ok": True,
        "user": {
            "user_id": user_id,
            "username": username,
            "roles": roles,
            "scopes": scopes,
        },
    }


def _create_session_for_user(user: Dict[str, Any]) -> Dict[str, Any]:
    token = f"tok_{new_uuid()}"
    refresh_token = f"rfr_{new_uuid()}"
    auth_session_id = f"sess_{new_uuid()}"
    now = int(time.time())

    session = {
        "auth_session_id": auth_session_id,
        "token": token,
        "refresh_token": refresh_token,
        "user_id": str(user.get("user_id", "")),
        "username": str(user.get("username", "")),
        "actor_id": f"user.{user.get('user_id', '')}",
        "actor_type": "user",
        "roles": _normalize_roles(user.get("roles", ["operator"])),
        "scopes": _normalize_scopes(user.get("scopes", [])),
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "expires_at": now + max(SESSION_TTL_SEC, 300),
        "refresh_expires_at": now + max(REFRESH_TTL_SEC, SESSION_TTL_SEC),
    }

    STATE.setdefault("sessions", {})[token] = session
    STATE.setdefault("refresh_index", {})[refresh_token] = token
    _persist_state()

    return {
        "token": token,
        "refresh_token": refresh_token,
        "expires_at": session["expires_at"],
        "refresh_expires_at": session["refresh_expires_at"],
        "session": session,
    }


def _invalidate_session(token: str) -> None:
    sessions = STATE.setdefault("sessions", {})
    refresh_index = STATE.setdefault("refresh_index", {})
    session = sessions.pop(token, None)
    if isinstance(session, dict):
        refresh_token = str(session.get("refresh_token", "")).strip()
        if refresh_token:
            refresh_index.pop(refresh_token, None)
    _persist_state()


def _refresh_session(refresh_token: str) -> Dict[str, Any]:
    refresh_index = STATE.setdefault("refresh_index", {})
    sessions = STATE.setdefault("sessions", {})
    old_token = str(refresh_index.get(refresh_token, "")).strip()
    if not old_token:
        return {"ok": False, "error": _auth_error("E_AUTH_REQUIRED", "refresh token is invalid")}

    session = sessions.get(old_token)
    if not isinstance(session, dict):
        refresh_index.pop(refresh_token, None)
        _persist_state()
        return {"ok": False, "error": _auth_error("E_AUTH_REQUIRED", "session not found")}

    now = int(time.time())
    if now >= int(session.get("refresh_expires_at", 0)):
        _invalidate_session(old_token)
        return {"ok": False, "error": _auth_error("E_AUTH_REQUIRED", "refresh token expired")}

    sessions.pop(old_token, None)
    token = f"tok_{new_uuid()}"
    session["token"] = token
    session["updated_at"] = now_iso()
    session["expires_at"] = now + max(SESSION_TTL_SEC, 300)
    sessions[token] = session
    refresh_index[refresh_token] = token
    _persist_state()

    return {
        "ok": True,
        "token": token,
        "refresh_token": refresh_token,
        "expires_at": session["expires_at"],
        "refresh_expires_at": session["refresh_expires_at"],
        "session": session,
    }


def _append_gateway_log(name: str, payload: Dict[str, Any]) -> None:
    PERSISTENCE.append_log(name, payload)


def _core_contract_request(
    *,
    auth_context: Dict[str, Any],
    conversation_id: str = "",
    body: Optional[Dict[str, Any]] = None,
    message: str = "",
    context: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    confirm: bool = False,
    approval_request_id: str = "",
) -> Dict[str, Any]:
    request_body = body if isinstance(body, dict) else {}
    request_context = context if isinstance(context, dict) else {}
    request_metadata = metadata if isinstance(metadata, dict) else {}
    return {
        "request_id": f"req_{new_uuid()}",
        "conversation_id": conversation_id,
        "auth_context": auth_context,
        "message": message,
        "context": request_context,
        "metadata": request_metadata,
        "confirm": confirm,
        "approval_request_id": approval_request_id,
        "adapter_contract_version": "v1",
        "core_contract_version": gateway_core.CORE_CONTRACT_VERSION,
    }


def _get_or_create_conversation(conversation_id: str, auth_context: Dict[str, Any], metadata: Dict[str, Any]) -> Dict[str, Any]:
    convo = gateway_core.get_or_create_conversation(
        state=STATE,
        conversation_id=conversation_id,
        auth_context=auth_context,
        metadata=metadata,
    )
    _persist_state()
    return convo


def _append_conversation_record(
    *,
    conversation_id: str,
    message: str,
    result: Dict[str, Any],
    auth_context: Dict[str, Any],
    metadata: Dict[str, Any],
) -> Dict[str, Any]:
    return gateway_core.append_conversation_record(
        state=STATE,
        persist_state=_persist_state,
        append_log=_append_gateway_log,
        conversation_id=conversation_id,
        message=message,
        result=result,
        auth_context=auth_context,
        metadata=metadata,
    )


def _enqueue_stream_event(conversation_id: str, event_type: str, payload: Dict[str, Any]) -> None:
    gateway_core.enqueue_stream_event(
        state=STATE,
        persist_state=_persist_state,
        conversation_id=conversation_id,
        event_type=event_type,
        payload=payload,
    )


def _pop_stream_events(conversation_id: str, limit: int = 200) -> List[Dict[str, Any]]:
    return gateway_core.pop_stream_events(
        state=STATE,
        persist_state=_persist_state,
        conversation_id=conversation_id,
        limit=limit,
    )


def _route_text_preview(route_response: Dict[str, Any]) -> str:
    return gateway_core.route_text_preview(route_response)


def _route_nl_message(body: Dict[str, Any], auth_context: Dict[str, Any], *, conversation_id: str) -> Dict[str, Any]:
    return gateway_core.route_nl_message(
        state=STATE,
        persist_state=_persist_state,
        append_log=_append_gateway_log,
        intent_router_base_url=INTENT_ROUTER_BASE_URL,
        http_timeout_sec=HTTP_TIMEOUT_SEC,
        body=body,
        auth_context=auth_context,
        conversation_id=conversation_id,
        library_root=str(LIBRARY_ROOT),
        provider_context_enabled=PROVIDER_CONTEXT_ENABLED,
        provider_context_max_turns=PROVIDER_CONTEXT_MAX_TURNS,
        provider_context_max_chars=PROVIDER_CONTEXT_MAX_CHARS,
        chat_sidecar_enabled=CHAT_SIDECAR_ENABLED,
        post_json=http_post_json,
    )


def _route_bdp(
    *,
    intent: str,
    payload: Dict[str, Any],
    auth_context: Dict[str, Any],
    conversation_id: str = "",
    confirm: bool = False,
    approval_request_id: str = "",
) -> Dict[str, Any]:
    return gateway_core.route_bdp(
        router_base_url=ROUTER_BASE_URL,
        http_timeout_sec=HTTP_TIMEOUT_SEC,
        intent=intent,
        payload=payload,
        auth_context=auth_context,
        conversation_id=conversation_id,
        confirm=confirm,
        approval_request_id=approval_request_id,
        post_json=http_post_json,
    )


def _handle_console_open(body: Dict[str, Any], auth_context: Dict[str, Any], conversation_id: str) -> Dict[str, Any]:
    request = _core_contract_request(
        auth_context=auth_context,
        conversation_id=conversation_id,
        body=body,
        context={
            "origin": str(body.get("origin", "")).strip(),
            "target": str(body.get("target", "")).strip(),
            "source_ip": str(body.get("source_ip", "")).strip(),
        },
        metadata=body.get("metadata", {}) if isinstance(body.get("metadata", {}), dict) else {},
    )
    return gateway_core.core_v1_console_open(
        state=STATE,
        persist_state=_persist_state,
        request=request,
        route_bdp_fn=_route_bdp,
    )


def _handle_console_close(body: Dict[str, Any], auth_context: Dict[str, Any], conversation_id: str) -> Dict[str, Any]:
    request = _core_contract_request(
        auth_context=auth_context,
        conversation_id=conversation_id,
        body=body,
        context={
            "console_session_id": str(body.get("console_session_id", "")).strip(),
            "reason": str(body.get("reason", "requested")).strip() or "requested",
        },
    )
    return gateway_core.core_v1_console_close(
        state=STATE,
        persist_state=_persist_state,
        request=request,
        route_bdp_fn=_route_bdp,
    )


def _handle_console_input(body: Dict[str, Any], auth_context: Dict[str, Any], conversation_id: str) -> Dict[str, Any]:
    event_payload = body.get("payload", {})
    if not isinstance(event_payload, dict):
        event_payload = {}
    text = str(body.get("text", "")).strip()
    if text:
        event_payload = {"data": text}

    request = _core_contract_request(
        auth_context=auth_context,
        conversation_id=conversation_id,
        body=body,
        message=text,
        context={
            "console_session_id": str(body.get("console_session_id", "")).strip(),
            "event": str(body.get("event", "terminal.input")).strip() or "terminal.input",
            "payload": event_payload,
        },
        confirm=bool(body.get("confirm", False)),
        approval_request_id=str(body.get("approval_request_id", "")).strip(),
    )
    return gateway_core.core_v1_console_input(
        state=STATE,
        persist_state=_persist_state,
        request=request,
        route_bdp_fn=_route_bdp,
    )


class GatewayHandler(BaseHTTPRequestHandler):
    server_version = "gateway.api/0.2"

    def _send_json(self, code: int, body: Dict[str, Any], extra_headers: Optional[Dict[str, str]] = None) -> None:
        payload = json.dumps(body, ensure_ascii=True).encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            if isinstance(extra_headers, dict):
                for key, value in extra_headers.items():
                    self.send_header(str(key), str(value))
            self.end_headers()
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

    def _send_html(self, code: int, html: str) -> None:
        payload = html.encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

    def _send_sse_response(self, events: List[Dict[str, Any]]) -> None:
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()

            for item in events:
                event_name = str(item.get("event", "message")).strip() or "message"
                payload = item.get("payload", {})
                if not isinstance(payload, dict):
                    payload = {"value": payload}
                frame = f"event: {event_name}\n" + f"data: {json.dumps(payload, ensure_ascii=True)}\n\n"
                self.wfile.write(frame.encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

    def _read_json(self) -> Optional[Dict[str, Any]]:
        try:
            size = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(size)
            parsed = json.loads(raw.decode("utf-8"))
        except Exception:
            return None
        return parsed if isinstance(parsed, dict) else None

    def _set_session_cookie(self, token: str) -> Dict[str, str]:
        return {"Set-Cookie": f"bd_session={token}; Path=/; HttpOnly; SameSite=Lax"}

    def _clear_session_cookie(self) -> Dict[str, str]:
        return {"Set-Cookie": "bd_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"}

    def _conversation_id(self, body: Dict[str, Any]) -> str:
        value = str(body.get("conversation_id", "")).strip()
        return value or f"conv_{new_uuid()}"

    @staticmethod
    def _core_status(result: Dict[str, Any]) -> int:
        if result.get("ok") is True:
            return 200
        error = result.get("error", {})
        code = str(error.get("code", "")) if isinstance(error, dict) else ""
        if code in {"E_BAD_MESSAGE", "E_AUTH_REQUIRED", "E_AUTH_FORBIDDEN"}:
            return 400
        return 502

    def _handle_auth_register(self, body: Dict[str, Any]) -> Tuple[int, Dict[str, Any], Dict[str, str]]:
        username = str(body.get("username", "")).strip()
        password = str(body.get("password", "")).strip()
        roles = _normalize_roles(body.get("roles", ["operator"]))
        scopes = _normalize_scopes(body.get("scopes", []))

        if not username or not password:
            return 400, {"ok": False, "error": _auth_error("E_BAD_MESSAGE", "username and password are required")}, {}

        result = _register_user(username, password, roles, scopes)
        status = 200 if result.get("ok") else 409
        return status, result, {}

    def _handle_auth_login(self, body: Dict[str, Any]) -> Tuple[int, Dict[str, Any], Dict[str, str]]:
        username = str(body.get("username", "")).strip()
        password = str(body.get("password", "")).strip()
        if not username or not password:
            return 400, {"ok": False, "error": _auth_error("E_BAD_MESSAGE", "username and password are required")}, {}

        user = _lookup_user(username)
        if not isinstance(user, dict):
            return 401, {"ok": False, "error": _auth_error("E_AUTH_REQUIRED", "invalid credentials")}, {}

        if _hash_password(password) != str(user.get("password_hash", "")):
            return 401, {"ok": False, "error": _auth_error("E_AUTH_REQUIRED", "invalid credentials")}, {}

        session = _create_session_for_user(user)
        response = {
            "ok": True,
            "session": {
                "auth_session_id": str(session["session"].get("auth_session_id", "")),
                "actor_id": str(session["session"].get("actor_id", "")),
                "roles": session["session"].get("roles", []),
                "scopes": session["session"].get("scopes", []),
                "expires_at": session.get("expires_at"),
                "refresh_expires_at": session.get("refresh_expires_at"),
            },
            "token": session.get("token"),
            "refresh_token": session.get("refresh_token"),
        }
        return 200, response, self._set_session_cookie(str(session.get("token", "")))

    def _handle_auth_logout(self) -> Tuple[int, Dict[str, Any], Dict[str, str]]:
        token = _session_token_from_request(self)
        if token:
            _invalidate_session(token)
        return 200, {"ok": True, "status": "logged_out"}, self._clear_session_cookie()

    def _handle_auth_refresh(self, body: Dict[str, Any]) -> Tuple[int, Dict[str, Any], Dict[str, str]]:
        refresh_token = str(body.get("refresh_token", "")).strip()
        if not refresh_token:
            token = _session_token_from_request(self)
            session = STATE.setdefault("sessions", {}).get(token)
            if isinstance(session, dict):
                refresh_token = str(session.get("refresh_token", "")).strip()

        if not refresh_token:
            return 401, {"ok": False, "error": _auth_error("E_AUTH_REQUIRED", "refresh token is required")}, {}

        refreshed = _refresh_session(refresh_token)
        if not refreshed.get("ok"):
            return 401, refreshed, {}

        response = {
            "ok": True,
            "token": refreshed.get("token"),
            "refresh_token": refreshed.get("refresh_token"),
            "expires_at": refreshed.get("expires_at"),
            "refresh_expires_at": refreshed.get("refresh_expires_at"),
        }
        return 200, response, self._set_session_cookie(str(refreshed.get("token", "")))

    def _handle_conversation_create(self, body: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        auth_context, auth_error, _ = _extract_auth_context(self, body, allow_session_fallback=False)
        if auth_error is not None:
            return 401, {"ok": False, "error": auth_error}

        assert auth_context is not None
        conversation_id = self._conversation_id(body)
        metadata = body.get("metadata", {}) if isinstance(body.get("metadata", {}), dict) else {}
        request = _core_contract_request(
            auth_context=auth_context,
            conversation_id=conversation_id,
            body=body,
            metadata=metadata,
        )
        result = gateway_core.core_v1_conversations_open(
            state=STATE,
            persist_state=_persist_state,
            request=request,
            strict=CORE_CONTRACT_STRICT,
        )
        return (200 if result.get("ok") else 400), result

    def _handle_messages(self, body: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        auth_context, auth_error, _ = _extract_auth_context(self, body, allow_session_fallback=False)
        if auth_error is not None:
            return 401, {"ok": False, "error": auth_error}

        assert auth_context is not None
        conversation_id = self._conversation_id(body)
        metadata = body.get("metadata", {}) if isinstance(body.get("metadata", {}), dict) else {}
        request = _core_contract_request(
            auth_context=auth_context,
            conversation_id=conversation_id,
            body=body,
            message=str(body.get("message", "")).strip(),
            context=body.get("context", {}) if isinstance(body.get("context", {}), dict) else {},
            metadata=metadata,
            confirm=bool(body.get("confirm", False)),
            approval_request_id=str(body.get("approval_request_id", "")).strip(),
        )
        result = gateway_core.core_v1_messages(
            state=STATE,
            persist_state=_persist_state,
            append_log=_append_gateway_log,
            request=request,
            intent_router_base_url=INTENT_ROUTER_BASE_URL,
            http_timeout_sec=HTTP_TIMEOUT_SEC,
            library_root=str(LIBRARY_ROOT),
            provider_context_enabled=PROVIDER_CONTEXT_ENABLED,
            provider_context_max_turns=PROVIDER_CONTEXT_MAX_TURNS,
            provider_context_max_chars=PROVIDER_CONTEXT_MAX_CHARS,
            chat_sidecar_enabled=CHAT_SIDECAR_ENABLED,
            post_json=http_post_json,
            strict=CORE_CONTRACT_STRICT,
        )
        return (200 if result.get("ok") else 502), result

    def _handle_console_open(self, body: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        auth_context, auth_error, _ = _extract_auth_context(self, body, allow_session_fallback=False)
        if auth_error is not None:
            return 401, {"ok": False, "error": auth_error}

        assert auth_context is not None
        conversation_id = self._conversation_id(body)
        metadata = body.get("metadata", {}) if isinstance(body.get("metadata", {}), dict) else {}
        _get_or_create_conversation(conversation_id, auth_context, metadata)
        result = _handle_console_open(body, auth_context, conversation_id)
        return (200 if result.get("ok") else 502), result

    def _conversation_for_console(self, body: Dict[str, Any]) -> str:
        console_session_id = str(body.get("console_session_id", "")).strip()
        if console_session_id:
            value = gateway_core.conversation_id_for_console_session(STATE, console_session_id)
            if value:
                return value
        return self._conversation_id(body)

    @staticmethod
    def _legacy_webterm_auth_context(
        actor_id_raw: Any, roles_raw: Any
    ) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        actor_id = str(actor_id_raw).strip()
        if not actor_id:
            return None, _auth_error("E_AUTH_REQUIRED", "actor_id is required")
        roles = _normalize_roles(roles_raw)
        return {
            "actor_id": actor_id,
            "actor_type": "user",
            "roles": roles,
            "scopes": [],
            "trace_id": str(new_uuid()),
            "auth_session_id": "",
        }, None

    @staticmethod
    def _as_bdp_error(code: str, message: str, *, details: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return {
            "protocol_version": "0.1",
            "message_id": str(new_uuid()),
            "intent": "error",
            "payload": {
                "error": {
                    "code": code,
                    "message": message,
                    "retryable": False,
                    "details": details or {},
                }
            },
        }

    def _webterm_response_from_console_result(self, result: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(result, dict):
            return self._as_bdp_error("E_NODE_ERROR", "invalid console response")
        if result.get("ok") is True:
            route_response = result.get("route_response", {})
            if isinstance(route_response, dict) and route_response:
                return route_response
            return self._as_bdp_error("E_NODE_ERROR", "console route response missing")
        error_obj = result.get("error", {})
        if isinstance(error_obj, dict):
            code = str(error_obj.get("code", "E_NODE_ERROR"))
            message = str(error_obj.get("message", "console request failed"))
        else:
            code = "E_NODE_ERROR"
            message = "console request failed"
        return self._as_bdp_error(code, message)

    def _handle_console_close(self, body: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        auth_context, auth_error, _ = _extract_auth_context(self, body, allow_session_fallback=False)
        if auth_error is not None:
            return 401, {"ok": False, "error": auth_error}

        assert auth_context is not None
        conversation_id = self._conversation_for_console(body)
        result = _handle_console_close(body, auth_context, conversation_id)
        return (200 if result.get("ok") else 502), result

    def _handle_console_input(self, body: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        auth_context, auth_error, _ = _extract_auth_context(self, body, allow_session_fallback=False)
        if auth_error is not None:
            return 401, {"ok": False, "error": auth_error}

        assert auth_context is not None
        conversation_id = self._conversation_for_console(body)
        result = _handle_console_input(body, auth_context, conversation_id)
        return (200 if result.get("ok") else 502), result

    def _handle_stream(self, conversation_id: str) -> None:
        if not conversation_id:
            self._send_sse_response([
                {
                    "event": "error",
                    "payload": {"code": "E_BAD_MESSAGE", "message": "conversation_id is required"},
                }
            ])
            return

        events = _pop_stream_events(conversation_id)
        if not events:
            events = [{"event": "complete", "payload": {"conversation_id": conversation_id, "status": "empty"}}]
        self._send_sse_response(events)

    def do_GET(self) -> None:
        path = _path_only(self.path)
        query = _query_values(self.path)

        if path in {"/health", "/api/v1/health"}:
            conversations = STATE.get("conversations", {})
            sessions = STATE.get("sessions", {})
            self._send_json(
                200,
                {
                    "ok": True,
                    "service": "gateway.api",
                    "service_role": "gateway.adapter",
                    "auth_required": AUTH_REQUIRED,
                    "enforce_session": ENFORCE_SESSION,
                    "legacy_gateway_routes_enabled": ENABLE_LEGACY_GATEWAY_ROUTES,
                    "gateway_core_contract_version": gateway_core.CORE_CONTRACT_VERSION,
                    "gateway_core_contract_strict": CORE_CONTRACT_STRICT,
                    "conversation_count": len(conversations) if isinstance(conversations, dict) else 0,
                    "session_count": len(sessions) if isinstance(sessions, dict) else 0,
                },
            )
            return

        if path.startswith("/core/v1/streams/"):
            conversation_id = path.removeprefix("/core/v1/streams/").strip()
            events = gateway_core.core_v1_stream_events(
                state=STATE,
                persist_state=_persist_state,
                conversation_id=conversation_id,
            )
            self._send_json(
                200,
                {
                    "ok": True,
                    "conversation_id": conversation_id,
                    "events": events,
                },
            )
            return

        if path in {"/api/v1/messages/stream", "/api/v1/console/stream"}:
            conversation_id = str(query.get("conversation_id", "")).strip()
            if not conversation_id:
                console_session_id = str(query.get("console_session_id", "")).strip()
                if console_session_id:
                    conversation_id = gateway_core.conversation_id_for_console_session(STATE, console_session_id)
            self._handle_stream(conversation_id)
            return

        if path == "/ui/terminal":
            self._send_html(200, WEB_TERMINAL_HTML)
            return

        if path == "/webterm/health":
            self._send_json(
                200,
                {
                    "ok": True,
                    "service": "gateway.api",
                    "ingress_owner": "gateway",
                },
            )
            return

        if path in {"/webterm/targets", "/webterm/guides"}:
            actor_id = str(query.get("actor_id", "")).strip()
            roles = [item.strip() for item in str(query.get("roles", "operator")).split(",") if item.strip()]
            auth_context, auth_error = self._legacy_webterm_auth_context(actor_id, roles)
            if auth_error is not None:
                self._send_json(401, self._as_bdp_error(auth_error.get("code", "E_AUTH_REQUIRED"), auth_error.get("message", "auth required")))
                return
            assert auth_context is not None
            intent = "web.console.targets.list" if path == "/webterm/targets" else "web.console.guides.list"
            try:
                response = _route_bdp(intent=intent, payload={}, auth_context=auth_context)
            except Exception as exc:
                response = self._as_bdp_error("E_NODE_UNAVAILABLE", f"route failed: {exc}")
            self._send_json(200, response if isinstance(response, dict) else self._as_bdp_error("E_NODE_ERROR", "invalid response"))
            return

        self._send_json(404, {"ok": False})

    def do_POST(self) -> None:
        path = _path_only(self.path)
        body = self._read_json()
        if body is None:
            self._send_json(400, {"ok": False, "error": {"code": "E_BAD_MESSAGE", "message": "Invalid JSON"}})
            return

        if path == "/core/v1/conversations/open":
            result = gateway_core.core_v1_conversations_open(
                state=STATE,
                persist_state=_persist_state,
                request=body,
                strict=CORE_CONTRACT_STRICT,
            )
            self._send_json(self._core_status(result), result)
            return

        if path == "/core/v1/messages":
            result = gateway_core.core_v1_messages(
                state=STATE,
                persist_state=_persist_state,
                append_log=_append_gateway_log,
                request=body,
                intent_router_base_url=INTENT_ROUTER_BASE_URL,
                http_timeout_sec=HTTP_TIMEOUT_SEC,
                library_root=str(LIBRARY_ROOT),
                provider_context_enabled=PROVIDER_CONTEXT_ENABLED,
                provider_context_max_turns=PROVIDER_CONTEXT_MAX_TURNS,
                provider_context_max_chars=PROVIDER_CONTEXT_MAX_CHARS,
                chat_sidecar_enabled=CHAT_SIDECAR_ENABLED,
                post_json=http_post_json,
                strict=CORE_CONTRACT_STRICT,
            )
            self._send_json(self._core_status(result), result)
            return

        if path == "/core/v1/console/open":
            result = gateway_core.core_v1_console_open(
                state=STATE,
                persist_state=_persist_state,
                request=body,
                route_bdp_fn=_route_bdp,
                strict=CORE_CONTRACT_STRICT,
            )
            self._send_json(self._core_status(result), result)
            return

        if path == "/core/v1/console/close":
            result = gateway_core.core_v1_console_close(
                state=STATE,
                persist_state=_persist_state,
                request=body,
                route_bdp_fn=_route_bdp,
                strict=CORE_CONTRACT_STRICT,
            )
            self._send_json(self._core_status(result), result)
            return

        if path == "/core/v1/console/input":
            result = gateway_core.core_v1_console_input(
                state=STATE,
                persist_state=_persist_state,
                request=body,
                route_bdp_fn=_route_bdp,
                strict=CORE_CONTRACT_STRICT,
            )
            self._send_json(self._core_status(result), result)
            return

        if path == "/webterm/session/open":
            actor_id = str(body.get("actor_id", "")).strip()
            auth_context, auth_error = self._legacy_webterm_auth_context(actor_id, body.get("roles", []))
            if auth_error is not None:
                self._send_json(401, self._as_bdp_error(auth_error.get("code", "E_AUTH_REQUIRED"), auth_error.get("message", "auth required")))
                return
            assert auth_context is not None
            conversation_id = self._conversation_id(body)
            metadata = body.get("metadata", {}) if isinstance(body.get("metadata", {}), dict) else {}
            _get_or_create_conversation(conversation_id, auth_context, metadata)
            result = _handle_console_open(body, auth_context, conversation_id)
            self._send_json(200, self._webterm_response_from_console_result(result))
            return

        if path == "/webterm/session/close":
            actor_id = str(body.get("actor_id", "")).strip()
            auth_context, auth_error = self._legacy_webterm_auth_context(actor_id, body.get("roles", []))
            if auth_error is not None:
                self._send_json(401, self._as_bdp_error(auth_error.get("code", "E_AUTH_REQUIRED"), auth_error.get("message", "auth required")))
                return
            assert auth_context is not None
            conversation_id = self._conversation_for_console(body)
            result = _handle_console_close(body, auth_context, conversation_id)
            self._send_json(200, self._webterm_response_from_console_result(result))
            return

        if path == "/webterm/session/event":
            actor_id = str(body.get("actor_id", "")).strip()
            auth_context, auth_error = self._legacy_webterm_auth_context(actor_id, body.get("roles", []))
            if auth_error is not None:
                self._send_json(401, self._as_bdp_error(auth_error.get("code", "E_AUTH_REQUIRED"), auth_error.get("message", "auth required")))
                return
            assert auth_context is not None
            conversation_id = self._conversation_for_console(body)
            result = _handle_console_input(body, auth_context, conversation_id)
            self._send_json(200, self._webterm_response_from_console_result(result))
            return

        if path == "/webterm/message":
            actor_id = str(body.get("actor_id", "")).strip()
            auth_context, auth_error = self._legacy_webterm_auth_context(actor_id, body.get("roles", []))
            if auth_error is not None:
                self._send_json(401, self._as_bdp_error(auth_error.get("code", "E_AUTH_REQUIRED"), auth_error.get("message", "auth required")))
                return
            assert auth_context is not None
            conversation_id = self._conversation_for_console(body)
            message_body = {
                "console_session_id": str(body.get("console_session_id", "")).strip(),
                "text": str(body.get("text", "")).strip(),
                "confirm": bool(body.get("confirm", False)),
                "approval_request_id": str(body.get("approval_request_id", "")).strip(),
            }
            result = _handle_console_input(message_body, auth_context, conversation_id)
            self._send_json(200, self._webterm_response_from_console_result(result))
            return

        if path == "/api/v1/auth/register":
            status, payload, headers = self._handle_auth_register(body)
            self._send_json(status, payload, headers)
            return

        if path == "/api/v1/auth/login":
            status, payload, headers = self._handle_auth_login(body)
            self._send_json(status, payload, headers)
            return

        if path == "/api/v1/auth/logout":
            status, payload, headers = self._handle_auth_logout()
            self._send_json(status, payload, headers)
            return

        if path == "/api/v1/auth/refresh":
            status, payload, headers = self._handle_auth_refresh(body)
            self._send_json(status, payload, headers)
            return

        if path == "/api/v1/conversations":
            status, payload = self._handle_conversation_create(body)
            self._send_json(status, payload)
            return

        if path == "/gateway/message":
            if not ENABLE_LEGACY_GATEWAY_ROUTES:
                self._send_json(
                    404,
                    {
                        "ok": False,
                        "error": {
                            "code": "E_DEPRECATED_ROUTE_DISABLED",
                            "message": "Legacy route /gateway/message is disabled; use /api/v1/messages",
                        },
                    },
                )
                return

        if path in {"/api/v1/messages", "/gateway/message"}:
            status, payload = self._handle_messages(body)
            if path == "/gateway/message" and isinstance(payload, dict):
                payload.setdefault("deprecation", "Route /gateway/message is deprecated; migrate to /api/v1/messages")
            self._send_json(status, payload)
            return

        if path in {"/api/v1/console/sessions/open"}:
            status, payload = self._handle_console_open(body)
            self._send_json(status, payload)
            return

        if path in {"/api/v1/console/sessions/close"}:
            status, payload = self._handle_console_close(body)
            self._send_json(status, payload)
            return

        if path in {"/api/v1/console/input"}:
            status, payload = self._handle_console_input(body)
            self._send_json(status, payload)
            return

        if path in {"/api/v1/messages/stream", "/api/v1/console/stream"}:
            conversation_id = str(body.get("conversation_id", "")).strip()
            if not conversation_id:
                console_session_id = str(body.get("console_session_id", "")).strip()
                if console_session_id:
                    conversation_id = gateway_core.conversation_id_for_console_session(STATE, console_session_id)
            self._handle_stream(conversation_id)
            return

        if path == "/gateway/stream":
            if not ENABLE_LEGACY_GATEWAY_ROUTES:
                self._send_sse_response(
                    [
                        {
                            "event": "error",
                            "payload": {
                                "code": "E_DEPRECATED_ROUTE_DISABLED",
                                "message": "Legacy route /gateway/stream is disabled; use /api/v1/messages/stream",
                            },
                        }
                    ]
                )
                return
            status, payload = self._handle_messages(body)
            if status != 200:
                self._send_sse_response([{"event": "error", "payload": payload.get("error", {})}])
                return
            conversation_id = str(payload.get("conversation_id", "")).strip()
            events = _pop_stream_events(conversation_id)
            self._send_sse_response(events if events else [{"event": "complete", "payload": payload}])
            return

        self._send_json(404, {"ok": False})

    def log_message(self, format: str, *args: Any) -> None:
        return


def main() -> None:
    if ENABLE_LEGACY_GATEWAY_ROUTES:
        print("gateway.api warning: legacy /gateway/* compatibility routes enabled")
    server = ThreadingHTTPServer(("0.0.0.0", PORT), GatewayHandler)
    print(f"gateway.api listening on :{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
