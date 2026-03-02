from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Dict, List

from ..protocol import make_error, make_response
from .base import ProtocolNode, cap


class GitOpsNode(ProtocolNode):
    node_id = "node.git.ops"
    priority = 120

    def capabilities(self) -> List:
        return [
            cap(
                name="git.init_if_needed",
                description="Initialize git repository when missing",
                input_schema={"type": "object"},
                risk_class="mutate",
                required_extensions=[],
                approval_required=False,
                examples=["initialize git"],
                idempotency="idempotent",
                side_effect_scope="file",
            ),
            cap(
                name="git.commit.approved_change",
                description="Commit approved file changes",
                input_schema={"type": "object", "required": ["paths", "commit_message"]},
                risk_class="mutate",
                required_extensions=[],
                approval_required=False,
                examples=["commit approved spec update"],
                idempotency="non_idempotent",
                side_effect_scope="file",
            ),
        ]

    def _git(self, *args: str) -> subprocess.CompletedProcess[str]:
        safe_dir = str(self.ctx.library_root)
        return subprocess.run(
            ["git", "-c", f"safe.directory={safe_dir}", "-C", str(self.ctx.library_root), *args],
            text=True,
            capture_output=True,
            check=False,
        )

    def _is_repo(self) -> bool:
        return (self.ctx.library_root / ".git").exists()

    def _safe_rel_path(self, raw: str) -> str:
        rel = raw.replace("\\", "/").strip()
        if not rel:
            raise ValueError("path cannot be empty")
        target = (self.ctx.library_root / rel).resolve()
        root = self.ctx.library_root.resolve()
        if target != root and root not in target.parents:
            raise ValueError("path traversal rejected")
        return rel

    def handle(self, message: Dict[str, Any]) -> Dict[str, Any]:
        intent = message.get("intent")
        payload = message.get("payload", {})
        if not isinstance(payload, dict):
            return make_error("E_BAD_MESSAGE", "payload must be object", message.get("message_id"))

        if intent == "git.init_if_needed":
            if self._is_repo():
                return make_response("git.ready", {"initialized": False}, message.get("message_id"))

            result = self._git("init")
            if result.returncode != 0:
                return make_error(
                    "E_NODE_ERROR",
                    "git init failed",
                    message.get("message_id"),
                    details={"stderr": result.stderr.strip()},
                )
            return make_response("git.ready", {"initialized": True}, message.get("message_id"))

        if intent == "git.commit.approved_change":
            if not self._is_repo():
                init = self._git("init")
                if init.returncode != 0:
                    return make_error("E_NODE_ERROR", "git init failed", message.get("message_id"))

            paths = payload.get("paths", [])
            if not isinstance(paths, list) or not paths:
                return make_error("E_BAD_MESSAGE", "paths must be non-empty list", message.get("message_id"))

            try:
                safe_paths = [self._safe_rel_path(str(item)) for item in paths]
            except ValueError as exc:
                return make_error("E_BAD_MESSAGE", str(exc), message.get("message_id"))

            add = self._git("add", *safe_paths)
            if add.returncode != 0:
                return make_error(
                    "E_NODE_ERROR",
                    "git add failed",
                    message.get("message_id"),
                    details={"stderr": add.stderr.strip()},
                )

            status = self._git("status", "--porcelain")
            if status.returncode != 0:
                return make_error("E_NODE_ERROR", "git status failed", message.get("message_id"))
            if not status.stdout.strip():
                return make_response("git.commit.skipped", {"reason": "no_changes"}, message.get("message_id"))

            commit_message = str(payload.get("commit_message", "")).strip()
            if not commit_message:
                return make_error("E_BAD_MESSAGE", "commit_message is required", message.get("message_id"))

            commit = subprocess.run(
                [
                    "git",
                    "-c",
                    f"safe.directory={self.ctx.library_root}",
                    "-C",
                    str(self.ctx.library_root),
                    "-c",
                    "user.name=BrainDrive",
                    "-c",
                    "user.email=braindrive@local",
                    "commit",
                    "-m",
                    commit_message,
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            if commit.returncode != 0:
                return make_error(
                    "E_NODE_ERROR",
                    "git commit failed",
                    message.get("message_id"),
                    details={"stderr": commit.stderr.strip()},
                )

            head = self._git("rev-parse", "HEAD")
            sha = head.stdout.strip() if head.returncode == 0 else ""
            return make_response(
                "git.committed",
                {
                    "paths": safe_paths,
                    "commit": sha,
                    "message": commit_message,
                },
                message.get("message_id"),
            )

        return make_error("E_NO_ROUTE", "Unsupported intent", message.get("message_id"))
