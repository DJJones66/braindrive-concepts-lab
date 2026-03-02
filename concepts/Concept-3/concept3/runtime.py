from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import ConfigResolver
from .intent_router import IntentRouterNL
from .metadata import NodeDescriptor
from .nodes import (
    ApprovalGateNode,
    AuditLogNode,
    ChatGeneralNode,
    FolderWorkflowNode,
    GitOpsNode,
    InterviewWorkflowNode,
    MemoryFsNode,
    OllamaModelNode,
    OpenRouterModelNode,
    PlanWorkflowNode,
    RuntimeBootstrapNode,
    SpecWorkflowNode,
)
from .nodes.base import NodeContext, ProtocolNode
from .persistence import Persistence
from .protocol import new_uuid
from .router import RouterCore
from .state import WorkflowState


@dataclass
class RegisteredNode:
    node: ProtocolNode
    descriptor: NodeDescriptor
    lease_token: str


class BrainDriveRuntime:
    def __init__(
        self,
        *,
        library_root: Path,
        data_root: Path,
        env: Optional[Dict[str, str]] = None,
        user_config_path: Optional[Path] = None,
        registration_token: str = "concept3-dev-token",
    ) -> None:
        self.library_root = library_root
        self.data_root = data_root
        self.library_root.mkdir(parents=True, exist_ok=True)
        self.data_root.mkdir(parents=True, exist_ok=True)

        self.env = dict(os.environ)
        if env:
            self.env.update(env)

        self.persistence = Persistence(self.data_root)
        self.workflow_state = WorkflowState(self.persistence)
        self.config = ConfigResolver(env=self.env, user_config_path=user_config_path)
        self.router = RouterCore(
            persistence=self.persistence,
            config=self.config,
            registration_token=registration_token,
            heartbeat_ttl_sec=15.0,
            library_root=self.library_root,
        )
        self.intent_router = IntentRouterNL(self.router)

        self.registration_token = registration_token
        self.test_endpoints_enabled = str(self.env.get("BRAINDRIVE_ENABLE_TEST_ENDPOINTS", "false")).lower() == "true"

        self.nodes: Dict[str, RegisteredNode] = {}
        self._register_default_nodes()

    def _ctx(self) -> NodeContext:
        return NodeContext(
            library_root=self.library_root,
            persistence=self.persistence,
            registration_token=self.registration_token,
            workflow_state=self.workflow_state,
            env=self.env,
            route_message=self.router.route,
        )

    def _register_default_nodes(self) -> None:
        defaults: List[ProtocolNode] = [
            ChatGeneralNode(self._ctx()),
            RuntimeBootstrapNode(self._ctx()),
            MemoryFsNode(self._ctx()),
            FolderWorkflowNode(self._ctx()),
            InterviewWorkflowNode(self._ctx()),
            SpecWorkflowNode(self._ctx()),
            PlanWorkflowNode(self._ctx()),
            ApprovalGateNode(self._ctx()),
            GitOpsNode(self._ctx()),
            OpenRouterModelNode(self._ctx()),
            OllamaModelNode(self._ctx()),
            AuditLogNode(self._ctx()),
        ]

        for node in defaults:
            self.register_node(node)

    def register_node(self, node: ProtocolNode) -> Dict[str, Any]:
        descriptor = node.descriptor()
        result = self.router.register_node(descriptor, node.handle)
        if result.get("ok"):
            self.nodes[descriptor.node_id] = RegisteredNode(
                node=node,
                descriptor=descriptor,
                lease_token=str(result.get("lease_token", "")),
            )
        return result

    def heartbeat_all(self) -> None:
        for item in self.nodes.values():
            self.router.heartbeat(item.descriptor.node_id, item.lease_token)

    def route(self, message: Dict[str, Any]) -> Dict[str, Any]:
        return self.router.route(message)

    def analyze(self, text: str) -> Dict[str, Any]:
        return self.intent_router.analyze(text)

    def route_nl(self, text: str, *, confirm: bool = False, extensions: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return self.intent_router.route(text, confirm=confirm, request_extensions=extensions)

    def bootstrap(self) -> Dict[str, Any]:
        bootstrap_message = {
            "protocol_version": "0.1",
            "message_id": new_uuid(),
            "intent": "system.bootstrap",
            "payload": {},
        }
        bootstrap = self.route(bootstrap_message)
        if bootstrap.get("intent") == "error":
            return bootstrap

        git_init = self.route(
            {
                "protocol_version": "0.1",
                "message_id": new_uuid(),
                "intent": "git.init_if_needed",
                "payload": {},
            }
        )

        selection = self.config.select_llm(None)
        provider_notice = self.config.startup_provider_notice(selection)

        return {
            "bootstrap": bootstrap,
            "git": git_init,
            "provider_notice": provider_notice,
        }

    def test_endpoint(self, endpoint: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not self.test_endpoints_enabled:
            return {"ok": False, "error": "test endpoints are disabled"}

        if endpoint == "/intent/analyze":
            return self.intent_router.analyze_endpoint(payload)
        if endpoint == "/intent/capabilities":
            return self.intent_router.capabilities()
        if endpoint == "/intent/test-route":
            message = payload.get("message", {})
            if not isinstance(message, dict):
                return {"ok": False, "error": "message must be object"}
            return {"ok": True, "response": self.intent_router.test_route(message)}

        return {"ok": False, "error": "unknown endpoint"}

    def apply_approval_flow(self, approval_request_payload: Dict[str, Any], approve: bool) -> Dict[str, Any]:
        request_response = self.route(
            {
                "protocol_version": "0.1",
                "message_id": new_uuid(),
                "intent": "approval.request",
                "payload": approval_request_payload,
            }
        )
        if request_response.get("intent") == "error":
            return {"approval_request": request_response}

        request_id = request_response.get("payload", {}).get("request_id", "")
        decision = "approved" if approve else "denied"

        resolve_response = self.route(
            {
                "protocol_version": "0.1",
                "message_id": new_uuid(),
                "intent": "approval.resolve",
                "payload": {
                    "request_id": request_id,
                    "decision": decision,
                    "decided_by": "owner",
                },
            }
        )
        if resolve_response.get("intent") == "error":
            return {"approval_request": request_response, "approval_resolve": resolve_response}

        out = {
            "approval_request": request_response,
            "approval_resolve": resolve_response,
        }

        if not approve:
            return out

        proposed_write = approval_request_payload.get("proposed_write", {})
        if isinstance(proposed_write, dict) and proposed_write.get("path") and proposed_write.get("content") is not None:
            write_response = self.route(
                {
                    "protocol_version": "0.1",
                    "message_id": new_uuid(),
                    "intent": "memory.write.propose",
                    "payload": {
                        "path": proposed_write.get("path"),
                        "content": proposed_write.get("content"),
                    },
                    "extensions": {
                        "confirmation": {
                            "required": True,
                            "status": "approved",
                            "request_id": request_id,
                        }
                    },
                }
            )
            out["write"] = write_response

            if write_response.get("intent") != "error":
                commit_response = self.route(
                    {
                        "protocol_version": "0.1",
                        "message_id": new_uuid(),
                        "intent": "git.commit.approved_change",
                        "payload": {
                            "paths": [str(proposed_write.get("path"))],
                            "reason": "approved_change",
                            "source_intent": str(approval_request_payload.get("intent_being_guarded", "unknown")),
                            "approval_request_id": request_id,
                            "commit_message": f"feat({Path(str(proposed_write.get('path'))).parent.name}): approved change",
                        },
                    }
                )
                out["commit"] = commit_response

        return out
