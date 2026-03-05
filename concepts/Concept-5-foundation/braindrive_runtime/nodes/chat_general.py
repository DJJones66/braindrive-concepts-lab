from __future__ import annotations

from typing import Any, Dict, List

from ..protocol import make_error, make_response, new_uuid
from ..workflow_conventions import load_workflow_conventions
from .base import ProtocolNode, cap


class ChatGeneralNode(ProtocolNode):
    node_id = "interface.cli"
    priority = 100

    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        self._workflow = load_workflow_conventions(self.ctx.library_root, self.ctx.persistence)

    def capabilities(self) -> List:
        return [
            cap(
                name="chat.general",
                description="General chat response",
                input_schema={"type": "object", "required": ["text"]},
                risk_class="read",
                required_extensions=[],
                approval_required=False,
                examples=["chat about my goals"],
                idempotency="idempotent",
                side_effect_scope="none",
            ),
            cap(
                name="runtime.cancel_generation",
                description="Cancel active generation",
                input_schema={"type": "object"},
                risk_class="read",
                required_extensions=[],
                approval_required=False,
                examples=["stop generating"],
                idempotency="idempotent",
                side_effect_scope="none",
            ),
            cap(
                name="runtime.compact_context",
                description="Compact current context when token budget is high",
                input_schema={"type": "object"},
                risk_class="read",
                required_extensions=[],
                approval_required=False,
                examples=["compact context"],
                idempotency="idempotent",
                side_effect_scope="none",
            ),
        ]

    def _route(self, intent: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if self.ctx.route_message is None:
            return {}
        return self.ctx.route_message(
            {
                "protocol_version": "0.1",
                "message_id": new_uuid(),
                "intent": intent,
                "payload": payload,
            }
        )

    def _active_folder(self) -> str:
        if self.ctx.route_message is not None:
            response = self._route("session.active_folder.get", {})
            if response.get("intent") == "session.active_folder":
                payload = response.get("payload", {})
                if isinstance(payload, dict):
                    value = payload.get("active_folder", "")
                    if isinstance(value, str):
                        return value
        return ""

    def _read_plan(self, active_folder: str) -> str:
        if not active_folder:
            return ""
        if self.ctx.route_message is not None:
            response = self._route("memory.read", {"path": f"{active_folder}/{self._workflow.plan_path}"})
            if response.get("intent") == "memory.read.result":
                payload = response.get("payload", {})
                if isinstance(payload, dict):
                    content = payload.get("content", "")
                    if isinstance(content, str):
                        return content
        return ""

    def handle(self, message: Dict[str, Any]) -> Dict[str, Any]:
        intent = message.get("intent")

        if intent == "runtime.cancel_generation":
            return make_response("runtime.cancelled", {"cancelled": True}, message.get("message_id"))

        if intent == "runtime.compact_context":
            return make_response(
                "runtime.context_compacted",
                {
                    "compacted": True,
                    "notice": "Conversation context was compacted to preserve responsiveness.",
                },
                message.get("message_id"),
            )

        if intent != "chat.general":
            return make_error("E_NO_ROUTE", "Unsupported intent", message.get("message_id"))

        text = str(message.get("payload", {}).get("text", ""))
        lowered = text.lower()

        if "what next" in lowered:
            active_folder = self._active_folder()
            if active_folder:
                plan_text = self._read_plan(active_folder)
                if plan_text:
                    bullets = [line.strip() for line in plan_text.splitlines() if line.strip().startswith("- ")]
                    next_steps = bullets[:3] if bullets else ["Review plan milestones and pick the top-priority task."]
                    return make_response(
                        "chat.response",
                        {
                            "text": "Next steps from your plan:",
                            "next_steps": next_steps,
                            "source": f"{active_folder}/{self._workflow.plan_path}",
                        },
                        message.get("message_id"),
                    )

        return make_response(
            "chat.response",
            {
                "text": text,
                "note": "Handled by interface.cli",
            },
            message.get("message_id"),
        )
