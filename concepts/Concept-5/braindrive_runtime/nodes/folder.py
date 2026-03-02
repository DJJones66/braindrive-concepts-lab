from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List

from ..protocol import make_error, make_response
from .base import ProtocolNode, cap


class FolderWorkflowNode(ProtocolNode):
    node_id = "node.workflow.folder"
    priority = 140

    def capabilities(self) -> List:
        return [
            cap(
                name="folder.create",
                description="Create topic folder with AGENT.md",
                input_schema={"type": "object", "required": ["topic"]},
                risk_class="mutate",
                required_extensions=[],
                approval_required=True,
                examples=["create folder for finances"],
                idempotency="non_idempotent",
                side_effect_scope="file",
            ),
            cap(
                name="folder.switch",
                description="Switch active folder context",
                input_schema={"type": "object", "required": ["folder"]},
                risk_class="read",
                required_extensions=[],
                approval_required=False,
                examples=["switch to finances"],
                idempotency="idempotent",
                side_effect_scope="none",
            ),
            cap(
                name="folder.list",
                description="List available folders",
                input_schema={"type": "object"},
                risk_class="read",
                required_extensions=[],
                approval_required=False,
                examples=["list folders"],
                idempotency="idempotent",
                side_effect_scope="none",
            ),
        ]

    def _state(self) -> Dict[str, Any]:
        if self.ctx.workflow_state is None:
            return {"active_folder": ""}
        return self.ctx.workflow_state.get()

    def _update_state(self, patch: Dict[str, Any]) -> None:
        if self.ctx.workflow_state is not None:
            self.ctx.workflow_state.update(patch)

    def _slug(self, text: str) -> str:
        slug = re.sub(r"[^a-zA-Z0-9\-_\s]", "", text.strip().lower())
        slug = re.sub(r"[\s_]+", "-", slug).strip("-")
        return slug or "untitled-topic"

    def _folders(self) -> List[str]:
        folders: List[str] = []
        for child in sorted(self.ctx.library_root.iterdir()):
            if not child.is_dir():
                continue
            if child.name.startswith("."):
                continue
            folders.append(child.name)
        return folders

    def _load_context_docs(self, folder_dir: Path) -> Dict[str, str]:
        docs = {}
        for filename in ["AGENT.md", "spec.md", "plan.md"]:
            path = folder_dir / filename
            if path.exists() and path.is_file():
                docs[filename] = path.read_text(encoding="utf-8")
        return docs

    def handle(self, message: Dict[str, Any]) -> Dict[str, Any]:
        intent = message.get("intent")
        payload = message.get("payload", {})
        if not isinstance(payload, dict):
            return make_error("E_BAD_MESSAGE", "payload must be object", message.get("message_id"))

        if intent == "folder.list":
            return make_response(
                "folder.listed",
                {
                    "folders": self._folders(),
                    "active_folder": self._state().get("active_folder", ""),
                },
                message.get("message_id"),
            )

        if intent == "folder.switch":
            folder = str(payload.get("folder", "")).strip()
            if not folder:
                return make_error("E_BAD_MESSAGE", "folder is required", message.get("message_id"))

            folder_dir = self.ctx.library_root / folder
            if not folder_dir.exists() or not folder_dir.is_dir():
                return make_error(
                    "E_NODE_ERROR",
                    f"Folder not found: {folder}",
                    message.get("message_id"),
                    details={"folders": self._folders()},
                )

            self._update_state({"active_folder": folder})
            return make_response(
                "folder.switched",
                {
                    "active_folder": folder,
                    "context_docs": self._load_context_docs(folder_dir),
                },
                message.get("message_id"),
            )

        if intent == "folder.create":
            confirmation = (message.get("extensions", {}) or {}).get("confirmation", {})
            if str(confirmation.get("status", "")).lower() != "approved":
                return make_error("E_CONFIRMATION_REQUIRED", "Approval required before folder creation", message.get("message_id"))

            topic = str(payload.get("topic", "")).strip()
            if not topic:
                return make_error("E_BAD_MESSAGE", "topic is required", message.get("message_id"))

            folder = str(payload.get("folder", "")).strip() or self._slug(topic)
            folder_dir = self.ctx.library_root / folder
            folder_dir.mkdir(parents=True, exist_ok=True)

            agent_path = folder_dir / "AGENT.md"
            if not agent_path.exists():
                agent_path.write_text(
                    (
                        f"# {topic}\n\n"
                        "## Purpose\n"
                        f"Working folder for {topic}.\n\n"
                        "## Protocol Rules\n"
                        "- No writes without explicit approval.\n"
                        "- Keep goals, context, and plan grounded in this folder.\n"
                    ),
                    encoding="utf-8",
                )

            self._update_state({"active_folder": folder})
            return make_response(
                "folder.created",
                {
                    "folder": folder,
                    "agent_path": str(agent_path.relative_to(self.ctx.library_root)).replace("\\", "/"),
                },
                message.get("message_id"),
            )

        return make_error("E_NO_ROUTE", "Unsupported intent", message.get("message_id"))
