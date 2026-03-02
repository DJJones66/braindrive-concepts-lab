from __future__ import annotations

import json
import os
import threading
import time
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from shared.bdp import (
    E_AUTHZ_DENIED,
    E_INTERNAL,
    E_NODE_ERROR,
    E_NODE_NOT_REGISTERED,
    E_NODE_REG_INVALID,
    E_NODE_UNAVAILABLE,
    E_NODE_UNTRUSTED,
    E_NO_ROUTE,
    E_POLICY_UNAVAILABLE,
    E_REQUIRED_EXTENSION_MISSING,
    E_UNSUPPORTED_PROTOCOL,
    append_jsonl,
    ensure_trace,
    http_post_json,
    looks_like_bdp,
    make_error,
    new_uuid,
    now_iso,
    validate_core,
)

PORT = int(os.getenv("ROUTER_PORT", "8080"))
NODE_TIMEOUT_SEC = float(os.getenv("ROUTER_NODE_TIMEOUT_SEC", "2.5"))
HEARTBEAT_TTL_SEC = float(os.getenv("ROUTER_HEARTBEAT_TTL_SEC", "15"))
CIRCUIT_FAIL_THRESHOLD = int(os.getenv("ROUTER_CIRCUIT_FAIL_THRESHOLD", "3"))
CIRCUIT_OPEN_SEC = float(os.getenv("ROUTER_CIRCUIT_OPEN_SEC", "12"))
REGISTRATION_TOKEN = os.getenv("ROUTER_REGISTRATION_TOKEN", "concept1-dev-token")
DATA_DIR = Path(os.getenv("ROUTER_DATA_DIR", "/workspace/data/router"))
ROUTER_LOG_FILE = DATA_DIR / "router-events.jsonl"

AUTH_URL = os.getenv("ROUTER_AUTH_URL", "http://node-auth-policy:8101/bdp")
AUDIT_URL = os.getenv("ROUTER_AUDIT_URL", "http://node-audit-log:8103/bdp")
ACTIVITY_URL = os.getenv("ROUTER_ACTIVITY_URL", "http://node-activity-feedback:8104/bdp")

MUTATING_INTENTS = {
    token.strip()
    for token in os.getenv("ROUTER_MUTATING_INTENTS", "workflow.page.create,workflow.interview.start").split(",")
    if token.strip()
}
DESTRUCTIVE_INTENTS = {
    token.strip() for token in os.getenv("ROUTER_DESTRUCTIVE_INTENTS", "memory.delete").split(",") if token.strip()
}

REGISTRY: Dict[str, Dict[str, Any]] = {}
NODE_HEALTH: Dict[str, Dict[str, Any]] = {}
LOCK = threading.Lock()


def parse_version(version: str) -> Tuple[int, int, int]:
    parts = version.split(".")
    ints: List[int] = []
    for part in parts[:3]:
        try:
            ints.append(int(part))
        except ValueError:
            ints.append(0)
    while len(ints) < 3:
        ints.append(0)
    return ints[0], ints[1], ints[2]


def _send_best_effort(url: str, intent: str, parent_message_id: Optional[str], payload: Dict[str, Any]) -> None:
    msg = {
        "protocol_version": "0.1",
        "message_id": new_uuid(),
        "intent": intent,
        "payload": payload,
        "extensions": {},
    }
    ensure_trace(msg, parent_message_id=parent_message_id, hop="node.router")
    try:
        _ = http_post_json(url, msg, timeout_sec=1.0)
    except Exception:
        return


def emit_event(event_type: str, parent_message_id: Optional[str], payload: Dict[str, Any]) -> None:
    record = {
        "ts": now_iso(),
        "event_type": event_type,
        "parent_message_id": parent_message_id,
        "payload": payload,
    }
    append_jsonl(ROUTER_LOG_FILE, record)

    _send_best_effort(AUDIT_URL, "audit.record", parent_message_id, {"source": "node.router", **record})
    _send_best_effort(ACTIVITY_URL, "activity.record", parent_message_id, {"source": "node.router", **record})


def _sanitize_capabilities(raw_caps: Any) -> Optional[List[Dict[str, Any]]]:
    if not isinstance(raw_caps, list) or not raw_caps:
        return None

    out: List[Dict[str, Any]] = []
    for item in raw_caps:
        if isinstance(item, str):
            out.append({"name": item, "required_extensions": []})
            continue
        if not isinstance(item, dict):
            return None
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            return None
        required = item.get("required_extensions", [])
        if not isinstance(required, list) or not all(isinstance(v, str) for v in required):
            return None
        out.append({"name": name.strip(), "required_extensions": sorted(set(required))})

    return out


def _validate_descriptor(descriptor: Dict[str, Any]) -> Optional[str]:
    required_fields = [
        "node_id",
        "node_version",
        "endpoint_url",
        "supported_protocol_versions",
        "capabilities",
        "auth",
    ]
    for field in required_fields:
        if field not in descriptor:
            return f"Missing field: {field}"

    if not isinstance(descriptor.get("node_id"), str) or not descriptor["node_id"].strip():
        return "node_id must be a non-empty string"
    if not isinstance(descriptor.get("node_version"), str):
        return "node_version must be a string"
    if not isinstance(descriptor.get("endpoint_url"), str) or not descriptor["endpoint_url"].startswith("http"):
        return "endpoint_url must be an http URL"

    protocols = descriptor.get("supported_protocol_versions")
    if not isinstance(protocols, list) or not protocols or not all(isinstance(v, str) for v in protocols):
        return "supported_protocol_versions must be a non-empty list of strings"

    if _sanitize_capabilities(descriptor.get("capabilities")) is None:
        return "capabilities must be a non-empty list of capability objects"

    auth = descriptor.get("auth")
    if not isinstance(auth, dict):
        return "auth must be an object"
    if auth.get("registration_token") != REGISTRATION_TOKEN:
        return "registration token invalid"

    return None


def _prune_stale_locked() -> None:
    now = time.time()
    stale = [node_id for node_id, node in REGISTRY.items() if float(node.get("expires_at_epoch", 0.0)) <= now]
    for node_id in stale:
        node = REGISTRY.pop(node_id)
        emit_event("router.node_tombstoned", None, {"node_id": node_id, "endpoint_url": node.get("endpoint_url")})


def _health_for(node_id: str) -> Dict[str, Any]:
    state = NODE_HEALTH.get(node_id)
    if state is None:
        state = {
            "success_count": 0,
            "failure_count": 0,
            "consecutive_failures": 0,
            "ewma_latency_ms": None,
            "circuit_open_until": 0.0,
            "updated_at": now_iso(),
        }
        NODE_HEALTH[node_id] = state
    return state


def _record_success(node_id: str, latency_ms: float) -> None:
    state = _health_for(node_id)
    state["success_count"] += 1
    state["consecutive_failures"] = 0

    ewma = state.get("ewma_latency_ms")
    if ewma is None:
        state["ewma_latency_ms"] = latency_ms
    else:
        state["ewma_latency_ms"] = (0.7 * float(ewma)) + (0.3 * latency_ms)

    state["updated_at"] = now_iso()


def _record_failure(node_id: str) -> None:
    state = _health_for(node_id)
    state["failure_count"] += 1
    state["consecutive_failures"] += 1
    if state["consecutive_failures"] >= CIRCUIT_FAIL_THRESHOLD:
        state["circuit_open_until"] = time.time() + CIRCUIT_OPEN_SEC
    state["updated_at"] = now_iso()


def _circuit_open(node_id: str) -> bool:
    state = _health_for(node_id)
    return float(state.get("circuit_open_until", 0.0)) > time.time()


def _capability_required_extensions(node: Dict[str, Any], capability: str) -> List[str]:
    for cap in node.get("capabilities", []):
        if cap.get("name") == capability:
            return list(cap.get("required_extensions", []))
    return []


def _node_sort_key(node: Dict[str, Any]) -> Tuple[float, int, int, int, int, str]:
    node_id = str(node.get("node_id"))
    health = _health_for(node_id)
    success_count = int(health.get("success_count", 0))
    failure_count = int(health.get("failure_count", 0))
    total = max(1, success_count + failure_count)
    success_rate = success_count / total
    latency = float(health.get("ewma_latency_ms") or 10_000.0)

    major, minor, patch = parse_version(str(node.get("node_version", "0.0.0")))
    return (
        -float(node.get("priority", 100)),
        -success_rate,
        latency,
        -major,
        -minor,
        -patch,
        node_id,
    )


def _derive_risk_class(message: Dict[str, Any]) -> str:
    ext = message.get("extensions", {}) or {}
    plan = ext.get("intent_plan") if isinstance(ext, dict) else None
    if isinstance(plan, dict):
        risk = plan.get("risk_class")
        if risk in {"read", "mutate", "destructive"}:
            return str(risk)

    intent = message.get("intent", "")
    if intent in DESTRUCTIVE_INTENTS:
        return "destructive"
    if intent in MUTATING_INTENTS:
        return "mutate"
    return "read"


def _preflight_policy(message: Dict[str, Any], risk_class: str) -> Optional[Dict[str, Any]]:
    if risk_class not in {"mutate", "destructive"}:
        return None

    req = {
        "protocol_version": message.get("protocol_version", "0.1"),
        "message_id": new_uuid(),
        "intent": "auth.authorize",
        "payload": {
            "requested_intent": message.get("intent"),
            "risk_class": risk_class,
        },
        "extensions": {
            "identity": (message.get("extensions", {}) or {}).get("identity"),
            "authz": (message.get("extensions", {}) or {}).get("authz"),
            "confirmation": (message.get("extensions", {}) or {}).get("confirmation"),
        },
    }
    ensure_trace(req, parent_message_id=message.get("message_id"), hop="node.router")

    try:
        decision = http_post_json(AUTH_URL, req, timeout_sec=NODE_TIMEOUT_SEC)
    except Exception as exc:
        return make_error(
            E_POLICY_UNAVAILABLE,
            "Policy precheck unavailable; protected action denied",
            message.get("message_id"),
            details={"error": str(exc), "risk_class": risk_class},
        )

    if not looks_like_bdp(decision):
        return make_error(
            E_POLICY_UNAVAILABLE,
            "Policy precheck returned invalid response",
            message.get("message_id"),
            details={"risk_class": risk_class},
        )

    if decision.get("intent") == "error":
        err = decision.get("payload", {}).get("error", {})
        return make_error(
            E_AUTHZ_DENIED,
            str(err.get("message", "Authorization denied")),
            message.get("message_id"),
            details={"upstream_error": err},
        )

    allowed = bool(decision.get("payload", {}).get("allowed", False))
    if not allowed:
        return make_error(
            E_AUTHZ_DENIED,
            "Authorization denied for protected action",
            message.get("message_id"),
            details={
                "risk_class": risk_class,
                "reason_codes": decision.get("payload", {}).get("reason_codes", []),
            },
        )

    return None


def _route_message(message: Dict[str, Any]) -> Dict[str, Any]:
    validation_error = validate_core(message)
    if validation_error:
        return validation_error

    msg_id = message.get("message_id")
    capability = message.get("intent")
    extensions = message.get("extensions", {}) or {}
    protocol_version = message.get("protocol_version")

    with LOCK:
        _prune_stale_locked()
        protocol_candidates = [
            deepcopy(node)
            for node in REGISTRY.values()
            if protocol_version in node.get("supported_protocol_versions", [])
        ]

    if not protocol_candidates:
        return make_error(
            E_UNSUPPORTED_PROTOCOL,
            f"No active nodes support protocol {protocol_version}",
            msg_id,
        )

    capable = [node for node in protocol_candidates if any(cap.get("name") == capability for cap in node.get("capabilities", []))]
    if not capable:
        return make_error(E_NO_ROUTE, f"No active node supports capability: {capability}", msg_id, details={"capability": capability})

    eligible: List[Dict[str, Any]] = []
    missing_union: List[str] = []
    for node in capable:
        required = _capability_required_extensions(node, capability)
        missing = [req for req in required if req not in extensions]
        if missing:
            missing_union.extend(missing)
            continue
        eligible.append(node)

    if not eligible:
        missing_union = sorted(set(missing_union))
        return make_error(
            E_REQUIRED_EXTENSION_MISSING,
            "Missing required extension(s): " + ", ".join(missing_union),
            msg_id,
            details={"missing": missing_union},
        )

    risk_class = _derive_risk_class(message)
    preflight_error = _preflight_policy(message, risk_class)
    if preflight_error:
        emit_event(
            "router.policy_denied",
            msg_id,
            {
                "capability": capability,
                "risk_class": risk_class,
                "error": preflight_error.get("payload", {}).get("error", {}),
            },
        )
        return preflight_error

    candidates = sorted(eligible, key=_node_sort_key)
    attempted: List[Dict[str, Any]] = []

    for node in candidates:
        node_id = node.get("node_id", "unknown")
        if _circuit_open(node_id):
            attempted.append({"node_id": node_id, "result": "circuit_open"})
            continue

        outbound = deepcopy(message)
        ensure_trace(outbound, parent_message_id=msg_id, hop="node.router")

        emit_event(
            "router.route_dispatched",
            msg_id,
            {
                "selected_node_id": node_id,
                "capability": capability,
                "risk_class": risk_class,
            },
        )

        started = time.perf_counter()
        try:
            response = http_post_json(node.get("endpoint_url", ""), outbound, timeout_sec=NODE_TIMEOUT_SEC)
            latency_ms = (time.perf_counter() - started) * 1000.0
            if not looks_like_bdp(response):
                raise RuntimeError("Node returned invalid BDP response")

            if response.get("intent") == "error":
                err = response.get("payload", {}).get("error", {})
                retryable = bool(err.get("retryable", False))
                if retryable:
                    _record_failure(node_id)
                    attempted.append({"node_id": node_id, "result": "retryable_error", "error": err})
                    emit_event(
                        "router.route_retried",
                        msg_id,
                        {
                            "selected_node_id": node_id,
                            "error": err,
                        },
                    )
                    continue

                _record_success(node_id, latency_ms)
                emit_event(
                    "router.route_complete",
                    msg_id,
                    {
                        "selected_node_id": node_id,
                        "response_intent": response.get("intent"),
                        "latency_ms": round(latency_ms, 2),
                    },
                )
                return response

            _record_success(node_id, latency_ms)
            emit_event(
                "router.route_complete",
                msg_id,
                {
                    "selected_node_id": node_id,
                    "response_intent": response.get("intent"),
                    "latency_ms": round(latency_ms, 2),
                },
            )
            return response
        except Exception as exc:
            _record_failure(node_id)
            attempted.append({"node_id": node_id, "result": "unavailable", "error": str(exc)})
            emit_event(
                "router.route_retried",
                msg_id,
                {
                    "selected_node_id": node_id,
                    "error": str(exc),
                },
            )

    return make_error(
        E_NODE_UNAVAILABLE,
        "No eligible nodes could successfully process the request",
        msg_id,
        retryable=True,
        details={"capability": capability, "attempted": attempted},
    )


class RouterHandler(BaseHTTPRequestHandler):
    server_version = "node.router/0.1"

    def _send_json(self, code: int, body: Dict[str, Any]) -> None:
        payload = json.dumps(body, ensure_ascii=True).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _read_json(self) -> Optional[Dict[str, Any]]:
        try:
            size = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(size)
            parsed = json.loads(raw.decode("utf-8"))
        except Exception:
            return None
        if not isinstance(parsed, dict):
            return None
        return parsed

    def do_GET(self) -> None:
        if self.path == "/health":
            with LOCK:
                _prune_stale_locked()
                registry_size = len(REGISTRY)
                capability_count = len(
                    {
                        cap.get("name")
                        for node in REGISTRY.values()
                        for cap in node.get("capabilities", [])
                        if isinstance(cap, dict)
                    }
                )
            self._send_json(
                200,
                {
                    "ok": True,
                    "service": "node.router",
                    "active_nodes": registry_size,
                    "capability_count": capability_count,
                },
            )
            return

        if self.path == "/router/registry":
            with LOCK:
                _prune_stale_locked()
                snapshot = []
                for node_id, node in REGISTRY.items():
                    item = deepcopy(node)
                    item["health"] = deepcopy(_health_for(node_id))
                    item.pop("lease_token", None)
                    snapshot.append(item)
            self._send_json(200, {"ok": True, "nodes": snapshot})
            return

        if self.path == "/router/catalog":
            with LOCK:
                _prune_stale_locked()
                catalog: Dict[str, List[Dict[str, Any]]] = {}
                for node in REGISTRY.values():
                    for cap in node.get("capabilities", []):
                        cap_name = cap.get("name")
                        if not cap_name:
                            continue
                        catalog.setdefault(cap_name, []).append(
                            {
                                "node_id": node.get("node_id"),
                                "priority": node.get("priority"),
                                "required_extensions": cap.get("required_extensions", []),
                                "supported_protocol_versions": node.get("supported_protocol_versions", []),
                            }
                        )
            self._send_json(200, {"ok": True, "catalog": catalog})
            return

        self._send_json(404, {"ok": False})

    def do_POST(self) -> None:
        if self.path == "/router/node/register":
            payload = self._read_json()
            if payload is None:
                self._send_json(400, {"ok": False, "error": "Invalid JSON"})
                return

            validation_error = _validate_descriptor(payload)
            if validation_error:
                code = 403 if "token" in validation_error else 400
                err_code = E_NODE_UNTRUSTED if code == 403 else E_NODE_REG_INVALID
                self._send_json(code, {"ok": False, "error": validation_error, "code": err_code})
                return

            node_id = str(payload["node_id"])
            lease_token = new_uuid()
            now_epoch = time.time()
            record = {
                "node_id": node_id,
                "node_version": payload.get("node_version"),
                "endpoint_url": payload.get("endpoint_url"),
                "supported_protocol_versions": list(payload.get("supported_protocol_versions", [])),
                "capabilities": _sanitize_capabilities(payload.get("capabilities")) or [],
                "priority": int(payload.get("priority", 100)),
                "region": payload.get("region", "local"),
                "cost_class": payload.get("cost_class", "standard"),
                "registered_at": now_iso(),
                "last_heartbeat_at": now_iso(),
                "expires_at": now_iso(),
                "expires_at_epoch": now_epoch + HEARTBEAT_TTL_SEC,
                "lease_token": lease_token,
                "status": "active",
            }

            with LOCK:
                REGISTRY[node_id] = record
                _health_for(node_id)

            emit_event(
                "router.node_registered",
                None,
                {
                    "node_id": node_id,
                    "endpoint_url": payload.get("endpoint_url"),
                    "capability_count": len(record["capabilities"]),
                },
            )

            self._send_json(
                200,
                {
                    "ok": True,
                    "node_id": node_id,
                    "lease_token": lease_token,
                    "heartbeat_ttl_sec": HEARTBEAT_TTL_SEC,
                },
            )
            return

        if self.path == "/router/node/heartbeat":
            payload = self._read_json()
            if payload is None:
                self._send_json(400, {"ok": False, "error": "Invalid JSON"})
                return

            node_id = payload.get("node_id")
            lease_token = payload.get("lease_token")
            if not isinstance(node_id, str) or not isinstance(lease_token, str):
                self._send_json(
                    400,
                    {"ok": False, "error": "node_id and lease_token are required", "code": E_NODE_REG_INVALID},
                )
                return

            with LOCK:
                node = REGISTRY.get(node_id)
                if not node:
                    self._send_json(404, {"ok": False, "error": "node not registered", "code": E_NODE_NOT_REGISTERED})
                    return
                if node.get("lease_token") != lease_token:
                    self._send_json(403, {"ok": False, "error": "invalid lease token", "code": E_NODE_UNTRUSTED})
                    return

                now_epoch = time.time()
                node["last_heartbeat_at"] = now_iso()
                node["expires_at"] = now_iso()
                node["expires_at_epoch"] = now_epoch + HEARTBEAT_TTL_SEC

            emit_event("router.node_heartbeat", None, {"node_id": node_id})
            self._send_json(200, {"ok": True, "node_id": node_id})
            return

        if self.path == "/route":
            message = self._read_json()
            if message is None:
                self._send_json(200, make_error("E_BAD_MESSAGE", "Invalid JSON body", None))
                return

            try:
                response = _route_message(message)
            except Exception as exc:
                response = make_error(
                    E_INTERNAL,
                    f"Router exception: {type(exc).__name__}",
                    message.get("message_id"),
                    details={"error": str(exc)},
                )
                emit_event(
                    "router.route_failed",
                    message.get("message_id"),
                    {"error": str(exc), "exception": type(exc).__name__},
                )

            self._send_json(200, response)
            return

        self._send_json(404, {"ok": False})

    def log_message(self, format: str, *args: Any) -> None:
        return


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("0.0.0.0", PORT), RouterHandler)
    print(f"node.router listening on :{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
