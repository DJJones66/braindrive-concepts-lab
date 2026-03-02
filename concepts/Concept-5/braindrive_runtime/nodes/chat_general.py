from __future__ import annotations

from typing import Dict, List

from ..protocol import make_error, make_response
from .base import ProtocolNode, cap


class ChatGeneralNode(ProtocolNode):
    node_id = "interface.cli"
    priority = 100

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

        if "what next" in lowered and self.ctx.workflow_state is not None:
            active_folder = str(self.ctx.workflow_state.read("active_folder", ""))
            if active_folder:
                plan_path = self.ctx.library_root / active_folder / "plan.md"
                if plan_path.exists() and plan_path.is_file():
                    plan_text = plan_path.read_text(encoding="utf-8")
                    bullets = [line.strip() for line in plan_text.splitlines() if line.strip().startswith("- ")]
                    next_steps = bullets[:3] if bullets else ["Review plan milestones and pick the top-priority task."]
                    return make_response(
                        "chat.response",
                        {
                            "text": "Next steps from your plan:",
                            "next_steps": next_steps,
                            "source": f"{active_folder}/plan.md",
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
