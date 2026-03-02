from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from ..state import WorkflowState

from ..constants import PROTOCOL_VERSION
from ..metadata import CapabilityMetadata, NodeDescriptor
from ..persistence import Persistence


@dataclass
class NodeContext:
    library_root: Path
    persistence: Persistence
    registration_token: str
    workflow_state: "WorkflowState | None" = None
    env: Mapping[str, str] | None = None
    route_message: Callable[[Dict[str, Any]], Dict[str, Any]] | None = None


class ProtocolNode:
    node_id: str
    node_version: str = "0.1.0"
    priority: int = 100

    def __init__(self, ctx: NodeContext) -> None:
        self.ctx = ctx

    def capabilities(self) -> List[CapabilityMetadata]:
        raise NotImplementedError

    def descriptor(self) -> NodeDescriptor:
        return NodeDescriptor(
            node_id=self.node_id,
            node_version=self.node_version,
            endpoint_url=f"inproc://{self.node_id}",
            supported_protocol_versions=[PROTOCOL_VERSION],
            capabilities=self.capabilities(),
            requires=[],
            priority=self.priority,
            auth={"registration_token": self.ctx.registration_token},
        )

    def handle(self, message: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError


def cap(
    name: str,
    description: str,
    input_schema: Dict[str, Any],
    risk_class: str,
    required_extensions: List[str],
    approval_required: bool,
    examples: List[str],
    idempotency: str,
    side_effect_scope: str,
    capability_version: str = "0.1.0",
    provider: str | None = None,
) -> CapabilityMetadata:
    return CapabilityMetadata(
        name=name,
        description=description,
        input_schema=input_schema,
        risk_class=risk_class,
        required_extensions=required_extensions,
        approval_required=approval_required,
        examples=examples,
        idempotency=idempotency,
        side_effect_scope=side_effect_scope,
        capability_version=capability_version,
        provider=provider,
    )
