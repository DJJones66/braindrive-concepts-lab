from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict

from shared.bdp import make_error, make_response, validate_core
from shared.node_runtime import start_registration_loop

PORT = int(os.getenv("CHAT_PORT", "8111"))
SERVICE_NAME = os.getenv("CHAT_SERVICE_NAME", "node.chat.general")
FAIL_PATTERN = os.getenv("CHAT_FAIL_PATTERN", "")


class ChatGeneralHandler(BaseHTTPRequestHandler):
    server_version = f"{SERVICE_NAME}/0.1"

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
            self._send_json(200, {"ok": True, "service": SERVICE_NAME})
            return
        self._send_json(404, {"ok": False})

    def do_POST(self) -> None:
        if self.path != "/bdp":
            self._send_json(404, {"ok": False})
            return

        message = self._read_json()
        if message is None:
            self._send_json(200, make_error("E_BAD_MESSAGE", "Invalid JSON body", None))
            return

        validation_error = validate_core(message)
        if validation_error:
            self._send_json(200, validation_error)
            return

        if message.get("intent") != "chat.general":
            self._send_json(
                200,
                make_error(
                    "E_NO_ROUTE",
                    f"{SERVICE_NAME} cannot handle intent {message.get('intent')}",
                    message.get("message_id"),
                ),
            )
            return

        extensions = message.get("extensions", {}) or {}
        identity = extensions.get("identity") if isinstance(extensions.get("identity"), dict) else {}
        actor_id = identity.get("actor_id")
        if not actor_id:
            self._send_json(
                200,
                make_error(
                    "E_REQUIRED_EXTENSION_MISSING",
                    "Missing required extension(s): identity",
                    message.get("message_id"),
                    details={"missing": ["identity"]},
                ),
            )
            return

        text = str(message.get("payload", {}).get("text", ""))
        if FAIL_PATTERN and FAIL_PATTERN in text:
            self._send_json(
                200,
                make_error(
                    "E_NODE_UNAVAILABLE",
                    f"{SERVICE_NAME} simulated temporary outage",
                    message.get("message_id"),
                    retryable=True,
                    details={"service": SERVICE_NAME},
                ),
            )
            return

        response_text = f"[{SERVICE_NAME}] {text}" if text else f"[{SERVICE_NAME}] (empty input)"

        self._send_json(
            200,
            make_response(
                "chat.response",
                {
                    "text": response_text,
                    "handled_by": SERVICE_NAME,
                    "actor_id": actor_id,
                },
                message.get("message_id"),
                extensions={"identity": identity},
            ),
        )

    def log_message(self, format: str, *args: Any) -> None:
        return


def main() -> None:
    start_registration_loop()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), ChatGeneralHandler)
    print(f"{SERVICE_NAME} listening on :{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
