from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from ..protocol import make_error, make_response
from .base import ProtocolNode, cap


class MemoryFsNode(ProtocolNode):
    node_id = "node.memory.fs"
    priority = 180

    def capabilities(self) -> List:
        return [
            cap(
                name="memory.read",
                description="Read file content from library",
                input_schema={"type": "object", "required": ["path"]},
                risk_class="read",
                required_extensions=[],
                approval_required=False,
                examples=["read finances/spec.md"],
                idempotency="idempotent",
                side_effect_scope="none",
            ),
            cap(
                name="memory.list",
                description="List files under library path",
                input_schema={"type": "object"},
                risk_class="read",
                required_extensions=[],
                approval_required=False,
                examples=["list files"],
                idempotency="idempotent",
                side_effect_scope="none",
            ),
            cap(
                name="memory.search",
                description="Search text in library files",
                input_schema={"type": "object", "required": ["query"]},
                risk_class="read",
                required_extensions=[],
                approval_required=False,
                examples=["search for milestone"],
                idempotency="idempotent",
                side_effect_scope="none",
            ),
            cap(
                name="memory.write.propose",
                description="Write content to file after approval",
                input_schema={"type": "object", "required": ["path", "content"]},
                risk_class="mutate",
                required_extensions=[],
                approval_required=True,
                examples=["save spec.md"],
                idempotency="non_idempotent",
                side_effect_scope="file",
            ),
            cap(
                name="memory.edit.propose",
                description="Edit existing file after approval",
                input_schema={"type": "object", "required": ["path"]},
                risk_class="mutate",
                required_extensions=[],
                approval_required=True,
                examples=["update plan.md"],
                idempotency="non_idempotent",
                side_effect_scope="file",
            ),
            cap(
                name="memory.delete.propose",
                description="Delete file after approval",
                input_schema={"type": "object", "required": ["path"]},
                risk_class="destructive",
                required_extensions=[],
                approval_required=True,
                examples=["delete old draft"],
                idempotency="non_idempotent",
                side_effect_scope="file",
            ),
        ]

    def _safe_path(self, relative_path: str) -> Path:
        rel = relative_path.strip().replace("\\", "/")
        if not rel:
            raise ValueError("path is required")

        target = (self.ctx.library_root / rel).resolve()
        root = self.ctx.library_root.resolve()
        if target != root and root not in target.parents:
            raise ValueError("path traversal rejected")
        return target

    def _confirmation_ok(self, message: Dict[str, Any]) -> bool:
        confirmation = (message.get("extensions", {}) or {}).get("confirmation", {})
        return isinstance(confirmation, dict) and str(confirmation.get("status", "")).lower() == "approved"

    def handle(self, message: Dict[str, Any]) -> Dict[str, Any]:
        intent = message.get("intent")
        payload = message.get("payload", {})

        if not isinstance(payload, dict):
            return make_error("E_BAD_MESSAGE", "payload must be object", message.get("message_id"))

        try:
            if intent == "memory.list":
                rel = str(payload.get("path", "."))
                base = self._safe_path(rel)
                if not base.exists() or not base.is_dir():
                    return make_error("E_NODE_ERROR", f"Directory not found: {rel}", message.get("message_id"))

                entries = []
                for child in sorted(base.iterdir()):
                    rel_path = child.resolve().relative_to(self.ctx.library_root.resolve())
                    entries.append(
                        {
                            "path": str(rel_path).replace("\\", "/"),
                            "is_dir": child.is_dir(),
                        }
                    )
                return make_response("memory.listed", {"entries": entries}, message.get("message_id"))

            if intent == "memory.read":
                rel = str(payload.get("path", ""))
                target = self._safe_path(rel)
                if not target.exists() or not target.is_file():
                    return make_error("E_NODE_ERROR", f"File not found: {rel}", message.get("message_id"))
                return make_response(
                    "memory.read.result",
                    {
                        "path": rel,
                        "content": target.read_text(encoding="utf-8"),
                    },
                    message.get("message_id"),
                )

            if intent == "memory.search":
                query = str(payload.get("query", "")).strip()
                if not query:
                    return make_error("E_BAD_MESSAGE", "query is required", message.get("message_id"))
                results = []
                for path in self.ctx.library_root.rglob("*.md"):
                    if not path.is_file():
                        continue
                    content = path.read_text(encoding="utf-8")
                    index = content.lower().find(query.lower())
                    if index == -1:
                        continue
                    rel_path = str(path.resolve().relative_to(self.ctx.library_root.resolve())).replace("\\", "/")
                    start = max(0, index - 40)
                    preview = content[start : index + 100].replace("\n", " ")
                    results.append({"path": rel_path, "preview": preview})
                return make_response("memory.search.results", {"query": query, "matches": results}, message.get("message_id"))

            if intent == "memory.write.propose":
                if not self._confirmation_ok(message):
                    return make_error("E_CONFIRMATION_REQUIRED", "Approval required before write", message.get("message_id"))

                rel = str(payload.get("path", "")).strip()
                content = str(payload.get("content", ""))
                target = self._safe_path(rel)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content if content.endswith("\n") else content + "\n", encoding="utf-8")
                self.ctx.persistence.emit_event("workflow", "memory.write", {"path": rel, "bytes": len(content.encode("utf-8"))})
                return make_response("memory.write.applied", {"path": rel}, message.get("message_id"))

            if intent == "memory.edit.propose":
                if not self._confirmation_ok(message):
                    return make_error("E_CONFIRMATION_REQUIRED", "Approval required before edit", message.get("message_id"))

                rel = str(payload.get("path", "")).strip()
                target = self._safe_path(rel)
                if not target.exists() or not target.is_file():
                    return make_error("E_NODE_ERROR", f"File not found: {rel}", message.get("message_id"))

                original = target.read_text(encoding="utf-8")
                if isinstance(payload.get("content"), str):
                    updated = str(payload.get("content"))
                elif isinstance(payload.get("find"), str) and isinstance(payload.get("replace"), str):
                    updated = original.replace(str(payload["find"]), str(payload["replace"]))
                else:
                    return make_error(
                        "E_BAD_MESSAGE",
                        "edit requires either content or find+replace",
                        message.get("message_id"),
                    )

                target.write_text(updated if updated.endswith("\n") else updated + "\n", encoding="utf-8")
                self.ctx.persistence.emit_event("workflow", "memory.edit", {"path": rel})
                return make_response("memory.edit.applied", {"path": rel}, message.get("message_id"))

            if intent == "memory.delete.propose":
                if not self._confirmation_ok(message):
                    return make_error("E_CONFIRMATION_REQUIRED", "Approval required before delete", message.get("message_id"))

                rel = str(payload.get("path", "")).strip()
                target = self._safe_path(rel)
                if not target.exists():
                    return make_error("E_NODE_ERROR", f"File not found: {rel}", message.get("message_id"))
                if target.is_dir():
                    return make_error("E_NODE_ERROR", "delete file intents cannot delete directories", message.get("message_id"))
                target.unlink(missing_ok=False)
                self.ctx.persistence.emit_event("workflow", "memory.delete", {"path": rel})
                return make_response("memory.delete.applied", {"path": rel}, message.get("message_id"))
        except ValueError as exc:
            return make_error("E_BAD_MESSAGE", str(exc), message.get("message_id"))
        except OSError as exc:
            return make_error("E_NODE_ERROR", f"filesystem error: {exc}", message.get("message_id"))

        return make_error("E_NO_ROUTE", "Unsupported intent", message.get("message_id"))
