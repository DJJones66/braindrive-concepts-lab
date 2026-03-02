#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional

from braindrive_runtime.constants import E_NODE_UNAVAILABLE
from braindrive_runtime.intent_router import IntentRouterNL
from braindrive_runtime.persistence import Persistence
from braindrive_runtime.protocol import http_get_json, http_post_json, make_error, validate_core

PORT = int(os.getenv("INTENT_ROUTER_PORT", "8081"))
ROUTER_BASE_URL = os.getenv("INTENT_ROUTER_ROUTER_BASE_URL", "http://node-router:8080")
ENABLE_TEST_ENDPOINTS = os.getenv("BRAINDRIVE_ENABLE_TEST_ENDPOINTS", "false").strip().lower() == "true"
CATALOG_TIMEOUT_SEC = float(os.getenv("INTENT_ROUTER_CATALOG_TIMEOUT_SEC", "2.0"))
ROUTE_TIMEOUT_SEC = float(os.getenv("INTENT_ROUTER_ROUTE_TIMEOUT_SEC", "60.0"))
RUNTIME_DIR = Path(os.getenv("BRAINDRIVE_RUNTIME_DIR", "/workspace/data/runtime"))
WORKFLOW_FULL_TRACE_ENABLED = os.getenv("BRAINDRIVE_WORKFLOW_FULL_TRACE", "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
TRACE_PERSISTENCE = Persistence(RUNTIME_DIR)


class HttpRouterAdapter:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def catalog(self) -> Dict[str, Any]:
        try:
            result = http_get_json(f"{self.base_url}/router/catalog", timeout_sec=CATALOG_TIMEOUT_SEC)
            if result.get("ok") is True and isinstance(result.get("catalog"), dict):
                return result["catalog"]
        except Exception:
            return {}
        return {}

    def route(self, message: Dict[str, Any]) -> Dict[str, Any]:
        try:
            return http_post_json(f"{self.base_url}/route", message, timeout_sec=ROUTE_TIMEOUT_SEC)
        except Exception as exc:
            return make_error(
                E_NODE_UNAVAILABLE,
                "router.core unavailable or timed out",
                message.get("message_id"),
                retryable=True,
                details={"error": str(exc)},
            )

    def route_for_test(self, message: Dict[str, Any]) -> Dict[str, Any]:
        return self.route(message)


INTENT_ROUTER = IntentRouterNL(HttpRouterAdapter(ROUTER_BASE_URL))


def _emit_workflow_full_trace(*, request_body: Dict[str, Any], response_body: Dict[str, Any]) -> None:
    if not WORKFLOW_FULL_TRACE_ENABLED:
        return
    try:
        TRACE_PERSISTENCE.emit_event(
            "workflow",
            "workflow.full_trace",
            {
                "source": "intent.route",
                "request": request_body,
                "response": response_body,
            },
        )
    except Exception:
        return


class IntentHandler(BaseHTTPRequestHandler):
    server_version = "intent.router.natural-language/0.3"

    def _send_json(self, code: int, body: Dict[str, Any]) -> None:
        payload = json.dumps(body, ensure_ascii=True).encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError, OSError):
            # Client disconnected before response write completed.
            return

    def _read_json(self) -> Optional[Dict[str, Any]]:
        try:
            size = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(size)
            parsed = json.loads(raw.decode("utf-8"))
        except Exception:
            return None
        return parsed if isinstance(parsed, dict) else None

    def _test_endpoints_enabled(self) -> bool:
        return ENABLE_TEST_ENDPOINTS

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json(
                200,
                {
                    "ok": True,
                    "service": "intent.router.natural-language",
                    "test_endpoints_enabled": self._test_endpoints_enabled(),
                },
            )
            return

        if self.path == "/intent/capabilities":
            if not self._test_endpoints_enabled():
                self._send_json(404, {"ok": False, "error": "test endpoints disabled"})
                return
            self._send_json(200, INTENT_ROUTER.capabilities())
            return

        self._send_json(404, {"ok": False})

    def do_POST(self) -> None:
        try:
            if self.path == "/intent/route":
                body = self._read_json()
                if body is None:
                    self._send_json(400, {"ok": False, "error": "Invalid JSON"})
                    return
                result = INTENT_ROUTER.route_endpoint(body)
                _emit_workflow_full_trace(request_body=body, response_body=result)
                self._send_json(200, result)
                return

            if self.path == "/intent/analyze":
                body = self._read_json()
                if body is None:
                    self._send_json(400, {"ok": False, "error": "Invalid JSON"})
                    return
                self._send_json(200, INTENT_ROUTER.analyze_endpoint(body))
                return

            if self.path == "/intent/test-route":
                if not self._test_endpoints_enabled():
                    self._send_json(404, {"ok": False, "error": "test endpoints disabled"})
                    return
                body = self._read_json()
                if body is None:
                    self._send_json(400, {"ok": False, "error": "Invalid JSON"})
                    return
                message = body.get("message", {})
                if not isinstance(message, dict):
                    self._send_json(400, {"ok": False, "error": "message must be object"})
                    return
                self._send_json(200, {"ok": True, "response": INTENT_ROUTER.test_route(message)})
                return

            if self.path == "/bdp":
                message = self._read_json()
                if message is None:
                    self._send_json(200, make_error("E_BAD_MESSAGE", "Invalid JSON body", None))
                    return

                validation_error = validate_core(message)
                if validation_error:
                    self._send_json(200, validation_error)
                    return

                self._send_json(200, INTENT_ROUTER.bdp_handle(message))
                return

            self._send_json(404, {"ok": False})
        except Exception as exc:
            self._send_json(
                200,
                {
                    "ok": False,
                    "error": f"intent.router.natural-language internal error: {type(exc).__name__}",
                    "details": {"message": str(exc)},
                },
            )

    def log_message(self, format: str, *args: Any) -> None:
        return


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", PORT), IntentHandler)
    print(f"intent.router.natural-language listening on :{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
