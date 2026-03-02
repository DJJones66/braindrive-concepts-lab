from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional

from .runtime import BrainDriveRuntime


class DebugIntentServer:
    def __init__(self, runtime: BrainDriveRuntime, host: str = "127.0.0.1", port: int = 9391) -> None:
        self.runtime = runtime
        self.host = host
        self.port = port

    def serve_forever(self) -> None:
        runtime = self.runtime

        class Handler(BaseHTTPRequestHandler):
            server_version = "braindrive_runtime.debug/0.1"

            def _read_json(self) -> Optional[Dict[str, Any]]:
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    raw = self.rfile.read(size)
                    parsed = json.loads(raw.decode("utf-8"))
                except Exception:
                    return None
                return parsed if isinstance(parsed, dict) else None

            def _send_json(self, code: int, body: Dict[str, Any]) -> None:
                payload = json.dumps(body, ensure_ascii=True).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self) -> None:
                if self.path == "/health":
                    self._send_json(200, {"ok": True, "service": "braindrive_runtime.debug"})
                    return
                if self.path == "/intent/capabilities":
                    self._send_json(200, runtime.test_endpoint("/intent/capabilities", {}))
                    return
                self._send_json(404, {"ok": False})

            def do_POST(self) -> None:
                body = self._read_json()
                if body is None:
                    self._send_json(400, {"ok": False, "error": "invalid json"})
                    return

                if self.path == "/intent/analyze":
                    self._send_json(200, runtime.test_endpoint("/intent/analyze", body))
                    return
                if self.path == "/intent/test-route":
                    self._send_json(200, runtime.test_endpoint("/intent/test-route", body))
                    return

                self._send_json(404, {"ok": False})

            def log_message(self, format: str, *args: Any) -> None:
                return

        server = ThreadingHTTPServer((self.host, self.port), Handler)
        print(f"Debug intent server listening on http://{self.host}:{self.port}")
        server.serve_forever()
