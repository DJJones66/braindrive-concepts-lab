from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

from ..protocol import make_error, make_response, new_uuid, now_iso
from .base import ProtocolNode, cap


class ApprovalGateNode(ProtocolNode):
    node_id = "node.approval.gate"
    priority = 190

    def __init__(self, ctx):
        super().__init__(ctx)
        loaded = self.ctx.persistence.load_state("approvals", {"requests": {}})
        self.state: Dict[str, Any] = loaded if isinstance(loaded, dict) else {"requests": {}}
        if not isinstance(self.state.get("requests"), dict):
            self.state["requests"] = {}

    def capabilities(self) -> List:
        return [
            cap(
                name="approval.request",
                description="Create approval request for pending mutation",
                input_schema={"type": "object", "required": ["intent_being_guarded", "changes"]},
                risk_class="read",
                required_extensions=[],
                approval_required=False,
                examples=["request approval for spec save"],
                idempotency="non_idempotent",
                side_effect_scope="file",
            ),
            cap(
                name="approval.resolve",
                description="Resolve approval request",
                input_schema={"type": "object", "required": ["request_id", "decision"]},
                risk_class="read",
                required_extensions=[],
                approval_required=False,
                examples=["approve request appr-123"],
                idempotency="non_idempotent",
                side_effect_scope="file",
            ),
        ]

    def _save(self) -> None:
        self.ctx.persistence.save_state("approvals", self.state)

    def _get_request(self, request_id: str) -> Dict[str, Any] | None:
        requests = self.state.get("requests", {})
        if not isinstance(requests, dict):
            return None
        item = requests.get(request_id)
        return item if isinstance(item, dict) else None

    def handle(self, message: Dict[str, Any]) -> Dict[str, Any]:
        intent = message.get("intent")
        payload = message.get("payload", {})

        if intent == "approval.request":
            if not isinstance(payload, dict):
                return make_error("E_BAD_MESSAGE", "payload must be object", message.get("message_id"))

            guarded = str(payload.get("intent_being_guarded", "")).strip()
            changes = payload.get("changes", [])
            if not guarded:
                return make_error("E_BAD_MESSAGE", "intent_being_guarded is required", message.get("message_id"))
            if not isinstance(changes, list) or not changes:
                return make_error("E_BAD_MESSAGE", "changes must be non-empty list", message.get("message_id"))

            request_id = str(payload.get("request_id", "")).strip() or f"appr-{new_uuid()}"
            record = {
                "request_id": request_id,
                "intent_being_guarded": guarded,
                "changes": deepcopy(changes),
                "status": "pending",
                "requested_at": now_iso(),
                "resolved_at": None,
                "decision": None,
                "decision_note": "",
            }

            self.state.setdefault("requests", {})[request_id] = record
            self._save()

            return make_response(
                "approval.requested",
                deepcopy(record),
                message.get("message_id"),
            )

        if intent == "approval.resolve":
            if not isinstance(payload, dict):
                return make_error("E_BAD_MESSAGE", "payload must be object", message.get("message_id"))

            request_id = str(payload.get("request_id", "")).strip()
            decision = str(payload.get("decision", "")).strip().lower()
            if not request_id:
                return make_error("E_BAD_MESSAGE", "request_id is required", message.get("message_id"))
            if decision not in {"approved", "denied"}:
                return make_error("E_BAD_MESSAGE", "decision must be approved|denied", message.get("message_id"))

            record = self._get_request(request_id)
            if not record:
                return make_error("E_NO_ROUTE", f"approval request not found: {request_id}", message.get("message_id"))

            record["status"] = decision
            record["decision"] = decision
            record["resolved_at"] = now_iso()
            record["decision_note"] = str(payload.get("decision_note", ""))
            record["decided_by"] = str(payload.get("decided_by", "owner"))
            self._save()

            return make_response(
                "approval.resolved",
                {
                    **deepcopy(record),
                    "confirmation": {
                        "required": True,
                        "status": decision,
                        "request_id": request_id,
                    },
                },
                message.get("message_id"),
            )

        return make_error("E_NO_ROUTE", "Unsupported intent", message.get("message_id"))
