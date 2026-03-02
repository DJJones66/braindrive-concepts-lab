from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .constants import MODEL_PROVIDERS, RISK_CLASSES


def parse_version(version: str) -> Tuple[int, int, int]:
    parts = version.split(".")
    values: List[int] = []
    for token in parts[:3]:
        try:
            values.append(int(token))
        except ValueError:
            values.append(0)
    while len(values) < 3:
        values.append(0)
    return values[0], values[1], values[2]


@dataclass
class CapabilityMetadata:
    name: str
    description: str
    input_schema: Dict[str, Any]
    risk_class: str
    required_extensions: List[str]
    approval_required: bool
    examples: List[str]
    idempotency: str
    side_effect_scope: str
    capability_version: str
    provider: Optional[str] = None

    def validate(self) -> Optional[str]:
        if not self.name.strip():
            return "capability.name must be non-empty"
        if self.risk_class not in RISK_CLASSES:
            return f"capability {self.name} has invalid risk_class"
        if not isinstance(self.input_schema, dict):
            return f"capability {self.name} input_schema must be object"
        if not isinstance(self.required_extensions, list) or not all(isinstance(v, str) for v in self.required_extensions):
            return f"capability {self.name} required_extensions must be list[str]"
        if not isinstance(self.examples, list) or not self.examples or not all(isinstance(v, str) for v in self.examples):
            return f"capability {self.name} examples must contain at least one string"
        if self.idempotency not in {"idempotent", "non_idempotent"}:
            return f"capability {self.name} invalid idempotency"
        if self.side_effect_scope not in {"none", "file", "external"}:
            return f"capability {self.name} invalid side_effect_scope"
        if self.provider is not None and self.provider not in MODEL_PROVIDERS:
            return f"capability {self.name} invalid provider"
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "risk_class": self.risk_class,
            "required_extensions": list(self.required_extensions),
            "approval_required": bool(self.approval_required),
            "examples": list(self.examples),
            "idempotency": self.idempotency,
            "side_effect_scope": self.side_effect_scope,
            "capability_version": self.capability_version,
            "provider": self.provider,
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "CapabilityMetadata":
        raw_provider = payload.get("provider")
        provider: Optional[str]
        if raw_provider is None:
            provider = None
        elif isinstance(raw_provider, str):
            provider = raw_provider.strip() or None
        else:
            provider = None

        return cls(
            name=str(payload.get("name", "")).strip(),
            description=str(payload.get("description", "")).strip(),
            input_schema=payload.get("input_schema", {}) if isinstance(payload.get("input_schema", {}), dict) else {},
            risk_class=str(payload.get("risk_class", "")).strip(),
            required_extensions=[str(v) for v in payload.get("required_extensions", []) if isinstance(v, str)],
            approval_required=bool(payload.get("approval_required", False)),
            examples=[str(v) for v in payload.get("examples", []) if isinstance(v, str)],
            idempotency=str(payload.get("idempotency", "")).strip(),
            side_effect_scope=str(payload.get("side_effect_scope", "")).strip(),
            capability_version=str(payload.get("capability_version", "")).strip(),
            provider=provider,
        )


@dataclass
class NodeDescriptor:
    node_id: str
    node_version: str
    endpoint_url: str
    supported_protocol_versions: List[str]
    capabilities: List[CapabilityMetadata]
    requires: List[str] = field(default_factory=list)
    priority: int = 100
    auth: Dict[str, Any] = field(default_factory=dict)

    def validate(self) -> Optional[str]:
        if not self.node_id.strip():
            return "node_id must be a non-empty string"
        if not self.node_version.strip():
            return "node_version must be a non-empty string"
        if not isinstance(self.endpoint_url, str) or not self.endpoint_url.strip():
            return "endpoint_url must be a non-empty string"
        if not self.supported_protocol_versions or not all(isinstance(v, str) and v.strip() for v in self.supported_protocol_versions):
            return "supported_protocol_versions must be a non-empty list[str]"
        if not self.capabilities:
            return "capabilities must be non-empty"
        for cap in self.capabilities:
            err = cap.validate()
            if err:
                return err
        if not isinstance(self.requires, list) or not all(isinstance(v, str) for v in self.requires):
            return "requires must be list[str]"
        if not isinstance(self.priority, int):
            return "priority must be int"
        if not isinstance(self.auth, dict):
            return "auth must be object"
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "node_version": self.node_version,
            "endpoint_url": self.endpoint_url,
            "supported_protocol_versions": list(self.supported_protocol_versions),
            "capabilities": [cap.to_dict() for cap in self.capabilities],
            "requires": list(self.requires),
            "priority": int(self.priority),
            "auth": dict(self.auth),
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "NodeDescriptor":
        capabilities_raw = payload.get("capabilities", [])
        capabilities: List[CapabilityMetadata] = []
        if isinstance(capabilities_raw, list):
            for item in capabilities_raw:
                if isinstance(item, dict):
                    capabilities.append(CapabilityMetadata.from_dict(item))
        return cls(
            node_id=str(payload.get("node_id", "")).strip(),
            node_version=str(payload.get("node_version", "")).strip(),
            endpoint_url=str(payload.get("endpoint_url", "")).strip(),
            supported_protocol_versions=[
                str(v) for v in payload.get("supported_protocol_versions", []) if isinstance(v, str)
            ],
            capabilities=capabilities,
            requires=[str(v) for v in payload.get("requires", []) if isinstance(v, str)],
            priority=int(payload.get("priority", 100)),
            auth=payload.get("auth", {}) if isinstance(payload.get("auth", {}), dict) else {},
        )
