from __future__ import annotations

from typing import Any, Dict, List

from ..protocol import make_error, make_response
from .base import ProtocolNode, cap


class AuditLogNode(ProtocolNode):
    node_id = "node.audit.log"
    priority = 50

    def capabilities(self) -> List:
        return [
            cap(
                name="audit.record",
                description="Append auditable event record",
                input_schema={"type": "object"},
                risk_class="read",
                required_extensions=[],
                approval_required=False,
                examples=["record audit event"],
                idempotency="idempotent",
                side_effect_scope="none",
            )
        ]

    def handle(self, message: Dict[str, Any]) -> Dict[str, Any]:
        if message.get("intent") != "audit.record":
            return make_error("E_NO_ROUTE", "Unsupported intent", message.get("message_id"))

        payload = message.get("payload", {})
        self.ctx.persistence.emit_event("audit", "audit.record", payload if isinstance(payload, dict) else {"value": payload})
        return make_response("audit.recorded", {"ok": True}, message.get("message_id"))
