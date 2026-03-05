#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Type

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from braindrive_runtime.nodes import (
    ApprovalGateNode,
    AuditLogNode,
    ChatGeneralNode,
    FolderWorkflowNode,
    GitOpsNode,
    MemoryFsNode,
    ModelProviderNode,
    RuntimeBootstrapNode,
    SessionStateNode,
    ScraplingNode,
    SkillWorkflowNode,
    WebConsoleNode,
)
from braindrive_runtime.nodes.base import NodeContext, ProtocolNode
from braindrive_runtime.persistence import Persistence
from braindrive_runtime.providers.registry import ModelProviderSpec, list_model_provider_specs
from braindrive_runtime.protocol import http_post_json, make_error, validate_core
from braindrive_runtime.security import validate_registration_token
from braindrive_runtime.service_registration import start_registration_loop
from braindrive_runtime.state import WorkflowState

PORT = int(os.getenv("NODE_PORT", "8110"))
NODE_KIND = os.getenv("NODE_KIND", "chat_general").strip().lower()
NODE_ENDPOINT_URL = os.getenv("NODE_ENDPOINT_URL", f"http://localhost:{PORT}/bdp")
try:
    REGISTRATION_TOKEN = validate_registration_token(os.getenv("ROUTER_REGISTRATION_TOKEN", ""))
except ValueError as exc:
    print(f"[error] {exc}", file=sys.stderr)
    raise SystemExit(2)
REGISTER_URL = os.getenv("ROUTER_REGISTER_URL", "http://node-router:8080/router/node/register")
HEARTBEAT_URL = os.getenv("ROUTER_HEARTBEAT_URL", "http://node-router:8080/router/node/heartbeat")
HEARTBEAT_SEC = float(os.getenv("ROUTER_HEARTBEAT_SEC", "5.0"))
REGISTER_RETRY_SEC = float(os.getenv("ROUTER_REGISTER_RETRY_SEC", "2.0"))
ROUTER_DIRECT_ROUTE_URL = os.getenv("ROUTER_DIRECT_ROUTE_URL", "http://node-router:8080/route")
ROUTER_DIRECT_ROUTE_TIMEOUT_SEC = float(os.getenv("ROUTER_DIRECT_ROUTE_TIMEOUT_SEC", "70.0"))

LIBRARY_ROOT = Path(os.getenv("BRAINDRIVE_LIBRARY_ROOT", "/workspace/data/library"))
RUNTIME_DIR = Path(os.getenv("BRAINDRIVE_RUNTIME_DIR", "/workspace/data/runtime"))

LIBRARY_ROOT.mkdir(parents=True, exist_ok=True)
RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

NODE_MAP: Dict[str, Type[ProtocolNode]] = {
    "runtime_bootstrap": RuntimeBootstrapNode,
    "memory_fs": MemoryFsNode,
    "folder": FolderWorkflowNode,
    "skill": SkillWorkflowNode,
    "session_state": SessionStateNode,
    "approval_gate": ApprovalGateNode,
    "git_ops": GitOpsNode,
    "scrapling": ScraplingNode,
    "web_console": WebConsoleNode,
    "chat_general": ChatGeneralNode,
    "audit_log": AuditLogNode,
}


def _provider_specs() -> Dict[str, ModelProviderSpec]:
    specs = list_model_provider_specs(dict(os.environ))
    return {spec.provider: spec for spec in specs}


def _provider_from_node_kind(node_kind: str, providers: Dict[str, ModelProviderSpec]) -> str:
    if node_kind in {"model_provider", "model"}:
        explicit = os.getenv("NODE_MODEL_PROVIDER", "").strip().lower()
        if explicit:
            return explicit
        return os.getenv("BRAINDRIVE_DEFAULT_PROVIDER", "").strip().lower()

    if node_kind.startswith("model_provider:"):
        return node_kind.partition(":")[2].strip().lower()

    if node_kind.startswith("model_"):
        candidate = node_kind[len("model_") :].strip().lower()
        if candidate in providers:
            return candidate
    return ""


def _build_model_provider_node(ctx: NodeContext, provider: str, providers: Dict[str, ModelProviderSpec]) -> ProtocolNode:
    spec = providers.get(provider)
    if spec is None:
        available = ", ".join(sorted(providers.keys()))
        raise ValueError(f"Unknown model provider '{provider}'. Available providers: {available}")
    return ModelProviderNode(
        ctx,
        provider=spec.provider,
        node_id=spec.node_id,
        priority=spec.priority,
        label=spec.label,
    )


def build_node() -> ProtocolNode:
    persistence = Persistence(RUNTIME_DIR)
    workflow_state = WorkflowState(persistence)

    def _route_message(message: Dict[str, Any]) -> Dict[str, Any]:
        return http_post_json(ROUTER_DIRECT_ROUTE_URL, message, timeout_sec=ROUTER_DIRECT_ROUTE_TIMEOUT_SEC)

    ctx = NodeContext(
        library_root=LIBRARY_ROOT,
        persistence=persistence,
        registration_token=REGISTRATION_TOKEN,
        workflow_state=workflow_state,
        env=dict(os.environ),
        route_message=_route_message,
    )

    providers = _provider_specs()
    provider = _provider_from_node_kind(NODE_KIND, providers)
    if provider:
        return _build_model_provider_node(ctx, provider, providers)

    node_cls = NODE_MAP.get(NODE_KIND)
    if node_cls is None:
        raise ValueError(f"Unknown NODE_KIND: {NODE_KIND}")
    return node_cls(ctx)


NODE = build_node()
DESCRIPTOR = NODE.descriptor()
DESCRIPTOR.endpoint_url = NODE_ENDPOINT_URL

start_registration_loop(
    descriptor=DESCRIPTOR.to_dict(),
    register_url=REGISTER_URL,
    heartbeat_url=HEARTBEAT_URL,
    heartbeat_sec=HEARTBEAT_SEC,
    register_retry_sec=REGISTER_RETRY_SEC,
)


class NodeHandler(BaseHTTPRequestHandler):
    server_version = f"{DESCRIPTOR.node_id}/0.1"

    def _send_json(self, code: int, body: Dict[str, Any]) -> None:
        payload = json.dumps(body, ensure_ascii=True).encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
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

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json(
                200,
                {
                    "ok": True,
                    "service": DESCRIPTOR.node_id,
                    "capabilities": [cap.name for cap in DESCRIPTOR.capabilities],
                },
            )
            return

        if self.path == "/descriptor":
            self._send_json(200, {"ok": True, "descriptor": DESCRIPTOR.to_dict()})
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

        try:
            response = NODE.handle(message)
        except Exception as exc:
            response = make_error(
                "E_INTERNAL",
                f"{DESCRIPTOR.node_id} exception: {type(exc).__name__}",
                message.get("message_id"),
                details={"error": str(exc)},
            )

        self._send_json(200, response)

    def log_message(self, format: str, *args: Any) -> None:
        return


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", PORT), NodeHandler)
    print(f"{DESCRIPTOR.node_id} ({NODE_KIND}) listening on :{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
