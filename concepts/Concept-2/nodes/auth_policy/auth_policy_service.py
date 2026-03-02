from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict

from shared.bdp import make_error, make_response, validate_core
from shared.node_runtime import start_registration_loop

PORT = int(os.getenv("AUTH_PORT", "8101"))

MODE_LOCK = threading.Lock()
MODE = os.getenv("AUTH_MODE", "allow").strip().lower()


def _current_mode() -> str:
    with MODE_LOCK:
        return MODE


def _set_mode(new_mode: str) -> bool:
    candidate = new_mode.strip().lower()
    if candidate not in {"allow", "deny", "down"}:
        return False
    with MODE_LOCK:
        global MODE
        MODE = candidate
    return True


def _decision(message: Dict[str, Any]) -> Dict[str, Any]:
    mode = _current_mode()
    payload = message.get("payload", {})
    risk_class = str(payload.get("risk_class", "read"))
    extensions = message.get("extensions", {}) or {}
    identity = extensions.get("identity") if isinstance(extensions.get("identity"), dict) else {}
    authz = extensions.get("authz") if isinstance(extensions.get("authz"), dict) else {}
    confirmation = extensions.get("confirmation") if isinstance(extensions.get("confirmation"), dict) else {}

    reason_codes = []

    if mode == "deny":
        reason_codes.append("policy_mode_deny")
        return make_response(
            "auth.decision",
            {
                "allowed": False,
                "mode": mode,
                "risk_class": risk_class,
                "reason_codes": reason_codes,
            },
            message.get("message_id"),
        )

    if not identity.get("actor_id"):
        reason_codes.append("identity_missing")
        return make_response(
            "auth.decision",
            {
                "allowed": False,
                "mode": mode,
                "risk_class": risk_class,
                "reason_codes": reason_codes,
            },
            message.get("message_id"),
        )

    if risk_class in {"mutate", "destructive"}:
        roles = identity.get("roles") if isinstance(identity.get("roles"), list) else []
        authz_allowed = bool(authz.get("decision") == "allow" or authz.get("approved") is True)
        role_allowed = "admin" in [str(role) for role in roles]

        if not (authz_allowed or role_allowed):
            reason_codes.append("authz_missing_or_denied")
            return make_response(
                "auth.decision",
                {
                    "allowed": False,
                    "mode": mode,
                    "risk_class": risk_class,
                    "reason_codes": reason_codes,
                },
                message.get("message_id"),
            )

    if risk_class == "destructive" and not bool(confirmation.get("confirmed", False)):
        reason_codes.append("confirmation_required")
        return make_response(
            "auth.decision",
            {
                "allowed": False,
                "mode": mode,
                "risk_class": risk_class,
                "reason_codes": reason_codes,
            },
            message.get("message_id"),
        )

    reason_codes.append("allow")
    return make_response(
        "auth.decision",
        {
            "allowed": True,
            "mode": mode,
            "risk_class": risk_class,
            "reason_codes": reason_codes,
        },
        message.get("message_id"),
    )


class AuthPolicyHandler(BaseHTTPRequestHandler):
    server_version = "node.auth.policy/0.1"

    def _send_json(self, code: int, body: Dict[str, Any]) -> None:
        payload = json.dumps(body, ensure_ascii=True).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _read_json(self) -> Dict[str, Any] | None:
        try:
            size = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(size)
            parsed = json.loads(raw.decode("utf-8"))
        except Exception:
            return None
        return parsed if isinstance(parsed, dict) else None

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json(200, {"ok": True, "service": "node.auth.policy", "mode": _current_mode()})
            return
        self._send_json(404, {"ok": False})

    def do_POST(self) -> None:
        if self.path == "/mode":
            body = self._read_json()
            if body is None:
                self._send_json(400, {"ok": False, "error": "Invalid JSON"})
                return
            if not _set_mode(str(body.get("mode", ""))):
                self._send_json(400, {"ok": False, "error": "Mode must be allow|deny|down"})
                return
            self._send_json(200, {"ok": True, "mode": _current_mode()})
            return

        if self.path != "/bdp":
            self._send_json(404, {"ok": False})
            return

        if _current_mode() == "down":
            self._send_json(503, {"ok": False, "error": "policy service unavailable (mode=down)"})
            return

        message = self._read_json()
        if message is None:
            self._send_json(200, make_error("E_BAD_MESSAGE", "Invalid JSON body", None))
            return

        validation_error = validate_core(message)
        if validation_error:
            self._send_json(200, validation_error)
            return

        if message.get("intent") != "auth.authorize":
            self._send_json(
                200,
                make_error("E_NO_ROUTE", f"node.auth.policy cannot handle intent {message.get('intent')}", message.get("message_id")),
            )
            return

        self._send_json(200, _decision(message))

    def log_message(self, format: str, *args: Any) -> None:
        return


def main() -> None:
    start_registration_loop()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), AuthPolicyHandler)
    print(f"node.auth.policy listening on :{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
