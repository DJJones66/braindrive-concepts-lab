from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict

from shared.bdp import append_jsonl, make_error, make_response, now_iso, validate_core
from shared.node_runtime import start_registration_loop

PORT = int(os.getenv("WORKFLOW_PORT", "8113"))
SERVICE_NAME = os.getenv("WORKFLOW_SERVICE_NAME", "node.workflow")
DATA_DIR = Path(os.getenv("WORKFLOW_DATA_DIR", "/workspace/data/workflow"))
LOG_FILE = DATA_DIR / "workflow-events.jsonl"

STATE_LOCK = threading.Lock()
COUNTERS = {
    "page": 0,
    "interview": 0,
    "delete": 0,
}


def _next_id(prefix: str) -> str:
    with STATE_LOCK:
        COUNTERS[prefix] += 1
        return f"{prefix}-{COUNTERS[prefix]}"


def _record(intent: str, message_id: str, payload: Dict[str, Any], extensions: Dict[str, Any]) -> None:
    append_jsonl(
        LOG_FILE,
        {
            "ts": now_iso(),
            "intent": intent,
            "message_id": message_id,
            "payload": payload,
            "extensions": extensions,
        },
    )


def _missing_extensions(message: Dict[str, Any], required: list[str]) -> list[str]:
    extensions = message.get("extensions", {}) or {}
    return [req for req in required if req not in extensions]


class WorkflowHandler(BaseHTTPRequestHandler):
    server_version = "node.workflow/0.1"

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

        intent = str(message.get("intent"))
        payload = message.get("payload", {})
        extensions = message.get("extensions", {}) or {}

        if intent in {"workflow.page.create", "workflow.interview.start", "memory.delete"}:
            missing = _missing_extensions(message, ["identity", "authz"])
            if missing:
                self._send_json(
                    200,
                    make_error(
                        "E_REQUIRED_EXTENSION_MISSING",
                        "Missing required extension(s): " + ", ".join(missing),
                        message.get("message_id"),
                        details={"missing": missing},
                    ),
                )
                return

        if intent == "workflow.page.create":
            title = str(payload.get("title", "Untitled Page"))
            page_id = _next_id("page")
            _record(intent, str(message.get("message_id")), payload, extensions)
            self._send_json(
                200,
                make_response(
                    "workflow.page.created",
                    {
                        "page_id": page_id,
                        "title": title,
                        "created_by": extensions.get("identity", {}).get("actor_id"),
                    },
                    message.get("message_id"),
                ),
            )
            return

        if intent == "workflow.interview.start":
            interview_id = _next_id("interview")
            topic = str(payload.get("topic", "general"))
            _record(intent, str(message.get("message_id")), payload, extensions)
            self._send_json(
                200,
                make_response(
                    "workflow.interview.started",
                    {
                        "interview_id": interview_id,
                        "topic": topic,
                        "question": f"What is the first objective for {topic}?",
                    },
                    message.get("message_id"),
                ),
            )
            return

        if intent == "workflow.plan.generate":
            missing = _missing_extensions(message, ["identity"])
            if missing:
                self._send_json(
                    200,
                    make_error(
                        "E_REQUIRED_EXTENSION_MISSING",
                        "Missing required extension(s): " + ", ".join(missing),
                        message.get("message_id"),
                        details={"missing": missing},
                    ),
                )
                return

            ref = str(payload.get("reference_spec_id", ""))
            source_text = str(payload.get("source_text", ""))
            _record(intent, str(message.get("message_id")), payload, extensions)
            summary = (
                f"Generated plan from spec {ref}."
                if ref
                else f"Generated plan from request: {source_text[:80]}"
            )
            self._send_json(
                200,
                make_response(
                    "workflow.plan.generated",
                    {
                        "summary": summary,
                        "handled_by": SERVICE_NAME,
                    },
                    message.get("message_id"),
                ),
            )
            return

        if intent == "memory.delete":
            confirmation = extensions.get("confirmation") if isinstance(extensions.get("confirmation"), dict) else {}
            if not bool(confirmation.get("confirmed", False)):
                self._send_json(
                    200,
                    make_error(
                        "E_CONFIRMATION_REQUIRED",
                        "memory.delete requires confirmation",
                        message.get("message_id"),
                    ),
                )
                return

            delete_id = _next_id("delete")
            target_id = str(payload.get("target_id", "unknown"))
            _record(intent, str(message.get("message_id")), payload, extensions)
            self._send_json(
                200,
                make_response(
                    "memory.deleted",
                    {
                        "delete_id": delete_id,
                        "target_id": target_id,
                        "status": "tombstoned",
                    },
                    message.get("message_id"),
                ),
            )
            return

        self._send_json(
            200,
            make_error(
                "E_NO_ROUTE",
                f"{SERVICE_NAME} cannot handle intent {intent}",
                message.get("message_id"),
            ),
        )

    def log_message(self, format: str, *args: Any) -> None:
        return


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    start_registration_loop()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), WorkflowHandler)
    print(f"{SERVICE_NAME} listening on :{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
