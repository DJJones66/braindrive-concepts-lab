from __future__ import annotations

import threading
import time
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from .constants import E_NODE_NOT_REGISTERED, E_NODE_REG_INVALID, E_NODE_UNTRUSTED
from .metadata import CapabilityMetadata, NodeDescriptor
from .persistence import Persistence
from .protocol import new_uuid, now_iso

NodeHandler = Callable[[Dict[str, Any]], Dict[str, Any]]


@dataclass
class NodeRecord:
    descriptor: NodeDescriptor
    handler: Optional[NodeHandler]
    lease_token: str
    expires_at_epoch: float
    registered_at: str
    last_heartbeat_at: str

    def to_public(self) -> Dict[str, Any]:
        descriptor = self.descriptor.to_dict()
        descriptor.update(
            {
                "lease_token": self.lease_token,
                "registered_at": self.registered_at,
                "last_heartbeat_at": self.last_heartbeat_at,
                "expires_at_epoch": self.expires_at_epoch,
                "status": "active" if self.expires_at_epoch > time.time() else "stale",
            }
        )
        return descriptor


class NodeRegistry:
    def __init__(self, persistence: Persistence, registration_token: str, heartbeat_ttl_sec: float = 15.0) -> None:
        self.persistence = persistence
        self.registration_token = registration_token
        self.heartbeat_ttl_sec = heartbeat_ttl_sec
        self.lock = threading.Lock()
        self.records: Dict[str, NodeRecord] = {}
        self.health: Dict[str, Dict[str, Any]] = {}
        self._load_snapshot()

    def register(self, descriptor: NodeDescriptor, handler: Optional[NodeHandler]) -> Dict[str, Any]:
        err = descriptor.validate()
        if err:
            return {"ok": False, "error": err, "code": E_NODE_REG_INVALID}

        provided_token = descriptor.auth.get("registration_token")
        if provided_token != self.registration_token:
            return {"ok": False, "error": "registration token invalid", "code": E_NODE_UNTRUSTED}

        now_epoch = time.time()
        lease_token = new_uuid()
        record = NodeRecord(
            descriptor=descriptor,
            handler=handler,
            lease_token=lease_token,
            expires_at_epoch=now_epoch + self.heartbeat_ttl_sec,
            registered_at=now_iso(),
            last_heartbeat_at=now_iso(),
        )

        with self.lock:
            self.records[descriptor.node_id] = record
            self.health.setdefault(
                descriptor.node_id,
                {
                    "success_count": 0,
                    "failure_count": 0,
                    "consecutive_failures": 0,
                    "ewma_latency_ms": None,
                    "circuit_open_until": 0.0,
                    "updated_at": now_iso(),
                },
            )
            self._save_snapshot_locked()

        self.persistence.emit_event(
            "router",
            "router.node_registered",
            {
                "node_id": descriptor.node_id,
                "capability_count": len(descriptor.capabilities),
            },
        )
        return {"ok": True, "node_id": descriptor.node_id, "lease_token": lease_token, "heartbeat_ttl_sec": self.heartbeat_ttl_sec}

    def heartbeat(self, node_id: str, lease_token: str) -> Dict[str, Any]:
        with self.lock:
            record = self.records.get(node_id)
            if not record:
                return {"ok": False, "error": "node not registered", "code": E_NODE_NOT_REGISTERED}
            if record.lease_token != lease_token:
                return {"ok": False, "error": "invalid lease token", "code": E_NODE_UNTRUSTED}

            now_epoch = time.time()
            record.last_heartbeat_at = now_iso()
            record.expires_at_epoch = now_epoch + self.heartbeat_ttl_sec
            self._save_snapshot_locked()

        return {"ok": True, "node_id": node_id}

    def prune_stale(self) -> None:
        with self.lock:
            now_epoch = time.time()
            stale = [node_id for node_id, rec in self.records.items() if rec.expires_at_epoch <= now_epoch]
            for node_id in stale:
                self.records.pop(node_id, None)
            if stale:
                self._save_snapshot_locked()

    def active_records(self) -> List[NodeRecord]:
        self.prune_stale()
        with self.lock:
            return [self._clone_record(rec) for rec in self.records.values()]

    def get_record(self, node_id: str) -> Optional[NodeRecord]:
        self.prune_stale()
        with self.lock:
            rec = self.records.get(node_id)
            return self._clone_record(rec) if rec else None

    def update_health(self, node_id: str, success: bool, latency_ms: Optional[float]) -> None:
        with self.lock:
            state = self.health.setdefault(
                node_id,
                {
                    "success_count": 0,
                    "failure_count": 0,
                    "consecutive_failures": 0,
                    "ewma_latency_ms": None,
                    "circuit_open_until": 0.0,
                    "updated_at": now_iso(),
                },
            )
            if success:
                state["success_count"] += 1
                state["consecutive_failures"] = 0
                if latency_ms is not None:
                    ewma = state.get("ewma_latency_ms")
                    state["ewma_latency_ms"] = latency_ms if ewma is None else (0.7 * float(ewma) + 0.3 * float(latency_ms))
            else:
                state["failure_count"] += 1
                state["consecutive_failures"] += 1
            state["updated_at"] = now_iso()
            self._save_snapshot_locked()

    def catalog(self) -> Dict[str, List[Dict[str, Any]]]:
        self.prune_stale()
        catalog: Dict[str, List[Dict[str, Any]]] = {}
        with self.lock:
            for rec in self.records.values():
                for cap in rec.descriptor.capabilities:
                    catalog.setdefault(cap.name, []).append(
                        {
                            "node_id": rec.descriptor.node_id,
                            "node_version": rec.descriptor.node_version,
                            "priority": rec.descriptor.priority,
                            "required_extensions": list(cap.required_extensions),
                            "risk_class": cap.risk_class,
                            "approval_required": cap.approval_required,
                            "provider": cap.provider,
                            "capability_version": cap.capability_version,
                        }
                    )
        return catalog

    def capability_metadata(self, intent: str) -> Optional[CapabilityMetadata]:
        self.prune_stale()
        candidates: List[tuple[int, tuple[int, int, int], str, CapabilityMetadata]] = []
        with self.lock:
            for rec in self.records.values():
                for cap in rec.descriptor.capabilities:
                    if cap.name == intent:
                        from .metadata import parse_version

                        candidates.append((rec.descriptor.priority, parse_version(rec.descriptor.node_version), rec.descriptor.node_id, cap))

        if not candidates:
            return None

        candidates.sort(key=lambda item: (-item[0], -item[1][0], -item[1][1], -item[1][2], item[2]))
        return deepcopy(candidates[0][3])

    def snapshot(self) -> Dict[str, Any]:
        self.prune_stale()
        with self.lock:
            nodes = []
            for node_id, rec in self.records.items():
                item = rec.to_public()
                item["health"] = deepcopy(self.health.get(node_id, {}))
                nodes.append(item)
            return {"nodes": nodes}

    def _snapshot_payload_locked(self) -> Dict[str, Any]:
        nodes = []
        for node_id, rec in self.records.items():
            node = rec.to_public()
            node["health"] = deepcopy(self.health.get(node_id, {}))
            nodes.append(node)
        return {"nodes": nodes}

    def _save_snapshot_locked(self) -> None:
        self.persistence.save_state("router_registry", self._snapshot_payload_locked())

    def _load_snapshot(self) -> None:
        payload = self.persistence.load_state("router_registry", {"nodes": []})
        if not isinstance(payload, dict):
            return
        nodes = payload.get("nodes", [])
        if not isinstance(nodes, list):
            return

        for item in nodes:
            if not isinstance(item, dict):
                continue
            try:
                descriptor = NodeDescriptor.from_dict(item)
            except Exception:
                continue
            if descriptor.validate():
                continue
            node_id = descriptor.node_id
            self.records[node_id] = NodeRecord(
                descriptor=descriptor,
                handler=None,
                lease_token=str(item.get("lease_token", "")),
                expires_at_epoch=float(item.get("expires_at_epoch", 0.0)),
                registered_at=str(item.get("registered_at", now_iso())),
                last_heartbeat_at=str(item.get("last_heartbeat_at", now_iso())),
            )
            health = item.get("health", {})
            self.health[node_id] = health if isinstance(health, dict) else {}

    def attach_handler(self, node_id: str, handler: NodeHandler) -> None:
        with self.lock:
            rec = self.records.get(node_id)
            if rec is None:
                return
            rec.handler = handler
            self._save_snapshot_locked()

    def _clone_record(self, rec: NodeRecord) -> NodeRecord:
        return NodeRecord(
            descriptor=NodeDescriptor.from_dict(rec.descriptor.to_dict()),
            handler=rec.handler,
            lease_token=rec.lease_token,
            expires_at_epoch=rec.expires_at_epoch,
            registered_at=rec.registered_at,
            last_heartbeat_at=rec.last_heartbeat_at,
        )
