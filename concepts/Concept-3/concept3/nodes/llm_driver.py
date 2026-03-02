from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from ..constants import E_NODE_ERROR, E_NODE_UNAVAILABLE
from ..protocol import make_error, new_uuid
from .base import NodeContext


def _strip_markdown_fence(text: str) -> str:
    cleaned = text.strip()
    if not cleaned.startswith("```"):
        return cleaned
    lines = cleaned.splitlines()
    if not lines:
        return cleaned
    body = lines[1:]
    if body and body[-1].strip().startswith("```"):
        body = body[:-1]
    return "\n".join(body).strip() or cleaned


class LLMSkillDriver:
    def __init__(self, ctx: NodeContext) -> None:
        self.ctx = ctx

    def load_skill(self, filename: str, parent_message_id: Optional[str]) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
        path = self.ctx.library_root / ".braindrive" / "skills" / filename
        if not path.exists() or not path.is_file():
            return None, make_error(
                E_NODE_ERROR,
                f"Skill file not found: {filename}",
                parent_message_id,
                retryable=False,
                details={"path": str(path)},
            )

        try:
            return path.read_text(encoding="utf-8"), None
        except Exception as exc:
            return None, make_error(
                E_NODE_ERROR,
                "Failed to read skill file",
                parent_message_id,
                retryable=False,
                details={"path": str(path), "error": str(exc)},
            )

    def complete(
        self,
        *,
        prompt: str,
        parent_message_id: Optional[str],
        llm_ext: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
        if self.ctx.route_message is None:
            return None, make_error(
                E_NODE_UNAVAILABLE,
                "LLM route is unavailable in this node context",
                parent_message_id,
                retryable=True,
            )

        message: Dict[str, Any] = {
            "protocol_version": "0.1",
            "message_id": new_uuid(),
            "intent": "model.chat.complete",
            "payload": {"prompt": prompt},
        }
        if isinstance(llm_ext, dict) and llm_ext:
            message["extensions"] = {"llm": llm_ext}

        try:
            response = self.ctx.route_message(message)
        except Exception as exc:
            return None, make_error(
                E_NODE_UNAVAILABLE,
                "Failed to call model node through router",
                parent_message_id,
                retryable=True,
                details={"error": str(exc)},
            )

        if not isinstance(response, dict):
            return None, make_error(
                E_NODE_ERROR,
                "Model route returned invalid response shape",
                parent_message_id,
                retryable=False,
            )

        if response.get("intent") == "error":
            return None, response

        if response.get("intent") != "model.chat.completed":
            return None, make_error(
                E_NODE_ERROR,
                f"Unexpected model response intent: {response.get('intent')}",
                parent_message_id,
                retryable=False,
            )

        payload = response.get("payload", {})
        if not isinstance(payload, dict):
            return None, make_error(
                E_NODE_ERROR,
                "Model response payload must be object",
                parent_message_id,
                retryable=False,
            )

        text = str(payload.get("text", "")).strip()
        if not text:
            return None, make_error(
                E_NODE_ERROR,
                "Model response text is empty",
                parent_message_id,
                retryable=False,
            )

        return _strip_markdown_fence(text), None


def read_context_doc(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""
