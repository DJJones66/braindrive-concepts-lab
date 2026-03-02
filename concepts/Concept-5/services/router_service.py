#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional

from braindrive_runtime.config import ConfigResolver
from braindrive_runtime.constants import E_NODE_REG_INVALID, E_NODE_UNTRUSTED
from braindrive_runtime.metadata import NodeDescriptor
from braindrive_runtime.persistence import Persistence
from braindrive_runtime.protocol import make_error
from braindrive_runtime.router import RouterCore

PORT = int(os.getenv("ROUTER_PORT", "8080"))
REGISTRATION_TOKEN = os.getenv("ROUTER_REGISTRATION_TOKEN", "braindrive-mvp-dev-token")
LIBRARY_ROOT = Path(os.getenv("BRAINDRIVE_LIBRARY_ROOT", "/workspace/data/library"))
RUNTIME_DIR = Path(os.getenv("BRAINDRIVE_RUNTIME_DIR", "/workspace/data/runtime"))
USER_CONFIG_PATH = Path(os.getenv("BRAINDRIVE_USER_CONFIG_PATH", "/workspace/data/runtime/user-config.yaml"))
NODE_TIMEOUT_SEC = float(os.getenv("ROUTER_NODE_TIMEOUT_SEC", "3.0"))

RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
LIBRARY_ROOT.mkdir(parents=True, exist_ok=True)

CONFIG = ConfigResolver(env=os.environ, user_config_path=USER_CONFIG_PATH)
PERSISTENCE = Persistence(RUNTIME_DIR)
ROUTER = RouterCore(
    persistence=PERSISTENCE,
    config=CONFIG,
    registration_token=REGISTRATION_TOKEN,
    heartbeat_ttl_sec=15.0,
    library_root=LIBRARY_ROOT,
    node_timeout_sec=NODE_TIMEOUT_SEC,
)


class RouterHandler(BaseHTTPRequestHandler):
    server_version = "router.core/0.3"

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

    def do_GET(self) -> None:
        if self.path == "/health":
            snapshot = ROUTER.registry_snapshot()
            self._send_json(
                200,
                {
                    "ok": True,
                    "service": "router.core",
                    "active_nodes": len(snapshot.get("nodes", [])),
                    "capability_count": len(ROUTER.catalog()),
                },
            )
            return

        if self.path == "/router/catalog":
            self._send_json(200, {"ok": True, "catalog": ROUTER.catalog()})
            return

        if self.path == "/router/registry":
            self._send_json(200, {"ok": True, **ROUTER.registry_snapshot()})
            return

        self._send_json(404, {"ok": False})

    def do_POST(self) -> None:
        if self.path == "/router/node/register":
            payload = self._read_json()
            if payload is None:
                self._send_json(400, {"ok": False, "error": "Invalid JSON"})
                return

            descriptor = NodeDescriptor.from_dict(payload)
            result = ROUTER.register_node(descriptor, None)
            if result.get("ok"):
                self._send_json(200, result)
                return

            code = result.get("code")
            if code == E_NODE_UNTRUSTED:
                self._send_json(403, result)
                return
            status = 400 if code == E_NODE_REG_INVALID else 500
            self._send_json(status, result)
            return

        if self.path == "/router/node/heartbeat":
            payload = self._read_json()
            if payload is None:
                self._send_json(400, {"ok": False, "error": "Invalid JSON"})
                return

            node_id = payload.get("node_id")
            lease_token = payload.get("lease_token")
            if not isinstance(node_id, str) or not isinstance(lease_token, str):
                self._send_json(400, {"ok": False, "error": "node_id and lease_token are required"})
                return

            result = ROUTER.heartbeat(node_id, lease_token)
            status = 200 if result.get("ok") else 404
            self._send_json(status, result)
            return

        if self.path == "/route":
            message = self._read_json()
            if message is None:
                self._send_json(200, make_error("E_BAD_MESSAGE", "Invalid JSON body", None))
                return

            try:
                response = ROUTER.route(message)
            except Exception as exc:
                response = make_error(
                    "E_INTERNAL",
                    f"Router exception: {type(exc).__name__}",
                    message.get("message_id"),
                    details={"error": str(exc)},
                )

            self._send_json(200, response)
            return

        self._send_json(404, {"ok": False})

    def log_message(self, format: str, *args: Any) -> None:
        return


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", PORT), RouterHandler)
    print(f"router.core listening on :{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
