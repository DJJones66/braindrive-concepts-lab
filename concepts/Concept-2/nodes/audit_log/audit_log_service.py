from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List

from shared.bdp import append_jsonl, make_error, make_response, now_iso, validate_core
from shared.node_runtime import start_registration_loop

PORT = int(os.getenv("AUDIT_PORT", "8103"))
DATA_DIR = Path(os.getenv("AUDIT_DATA_DIR", "/workspace/data/audit"))
LOG_FILE = DATA_DIR / "audit-events.jsonl"


def _tail_recent(limit: int) -> List[Dict[str, Any]]:
    if not LOG_FILE.exists():
        return []

    lines = LOG_FILE.read_text(encoding="utf-8").splitlines()
    selected = lines[-max(0, limit) :]
    output: List[Dict[str, Any]] = []
    for line in selected:
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            output.append(parsed)
    return output


class AuditLogHandler(BaseHTTPRequestHandler):
    server_version = "node.audit.log/0.1"

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
            self._send_json(200, {"ok": True, "service": "node.audit.log", "log_file": str(LOG_FILE)})
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

        if intent == "audit.record":
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            append_jsonl(
                LOG_FILE,
                {
                    "ts": now_iso(),
                    "message_id": message.get("message_id"),
                    "payload": payload,
                    "trace": (message.get("extensions", {}) or {}).get("trace", {}),
                },
            )
            self._send_json(200, make_response("audit.ack", {"ok": True}, message.get("message_id")))
            return

        if intent == "auth.audit.query":
            limit = int(payload.get("limit", 25)) if str(payload.get("limit", "")).isdigit() else 25
            self._send_json(
                200,
                make_response(
                    "audit.recent",
                    {
                        "events": _tail_recent(limit=min(max(limit, 1), 200)),
                    },
                    message.get("message_id"),
                ),
            )
            return

        self._send_json(
            200,
            make_error(
                "E_NO_ROUTE",
                f"node.audit.log cannot handle intent {intent}",
                message.get("message_id"),
            ),
        )

    def log_message(self, format: str, *args: Any) -> None:
        return


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    start_registration_loop()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), AuditLogHandler)
    print(f"node.audit.log listening on :{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
