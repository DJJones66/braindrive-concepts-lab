from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict

from shared.bdp import make_error, make_response, validate_core
from shared.node_runtime import start_registration_loop

PORT = int(os.getenv("SESSION_PORT", "8102"))

STATE_LOCK = threading.Lock()
CONTEXT_BY_CONVERSATION: Dict[str, Dict[str, Any]] = {
    "demo-conv-1": {
        "project_slug": "brain-drive",
        "last_topic": "Q2 roadmap",
        "last_page_id": "page-demo-1",
        "last_spec_id": "spec-demo-7",
        "last_draft_id": "draft-demo-3",
        "refs": ["spec-demo-7", "plan-demo-2"],
        "tags": ["demo", "router"],
    }
}


def _default_context(conversation_id: str) -> Dict[str, Any]:
    return {
        "project_slug": "brain-drive",
        "last_topic": "general",
        "last_page_id": f"page-{conversation_id}",
        "last_spec_id": "",
        "last_draft_id": "",
        "refs": [],
        "tags": [],
    }


def _get_context(conversation_id: str) -> Dict[str, Any]:
    with STATE_LOCK:
        if conversation_id not in CONTEXT_BY_CONVERSATION:
            CONTEXT_BY_CONVERSATION[conversation_id] = _default_context(conversation_id)
        return dict(CONTEXT_BY_CONVERSATION[conversation_id])


def _set_context(conversation_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    with STATE_LOCK:
        if conversation_id not in CONTEXT_BY_CONVERSATION:
            CONTEXT_BY_CONVERSATION[conversation_id] = _default_context(conversation_id)
        CONTEXT_BY_CONVERSATION[conversation_id].update(patch)
        return dict(CONTEXT_BY_CONVERSATION[conversation_id])


class SessionStateHandler(BaseHTTPRequestHandler):
    server_version = "node.session.state/0.1"

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
            with STATE_LOCK:
                count = len(CONTEXT_BY_CONVERSATION)
            self._send_json(200, {"ok": True, "service": "node.session.state", "contexts": count})
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

        intent = message.get("intent")
        payload = message.get("payload", {})

        if intent == "session.context.get":
            conversation_id = str(payload.get("conversation_id", "default-conversation"))
            context = _get_context(conversation_id)
            self._send_json(
                200,
                make_response(
                    "session.context",
                    {"conversation_id": conversation_id, "context": context},
                    message.get("message_id"),
                ),
            )
            return

        if intent == "session.context.set":
            conversation_id = str(payload.get("conversation_id", "default-conversation"))
            patch = payload.get("context", {})
            if not isinstance(patch, dict):
                self._send_json(
                    200,
                    make_error("E_BAD_MESSAGE", "context must be an object", message.get("message_id")),
                )
                return
            updated = _set_context(conversation_id, patch)
            self._send_json(
                200,
                make_response(
                    "session.context.updated",
                    {"conversation_id": conversation_id, "context": updated},
                    message.get("message_id"),
                ),
            )
            return

        self._send_json(
            200,
            make_error(
                "E_NO_ROUTE",
                f"node.session.state cannot handle intent {intent}",
                message.get("message_id"),
            ),
        )

    def log_message(self, format: str, *args: Any) -> None:
        return


def main() -> None:
    start_registration_loop()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), SessionStateHandler)
    print(f"node.session.state listening on :{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
