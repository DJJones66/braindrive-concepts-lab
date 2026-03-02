from __future__ import annotations

import time
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .config import ConfigResolver
from .constants import (
    E_BAD_MESSAGE,
    E_CONFIRMATION_REQUIRED,
    E_INTERNAL,
    E_NO_ROUTE,
    E_NODE_ERROR,
    E_NODE_UNAVAILABLE,
    E_REQUIRED_EXTENSION_MISSING,
    E_UNSUPPORTED_PROTOCOL,
    MODEL_PROVIDERS,
    PROTOCOL_VERSION,
)
from .metadata import NodeDescriptor, parse_version
from .persistence import Persistence
from .protocol import ensure_trace, looks_like_bdp, make_error, validate_core
from .protocol import http_post_json
from .registry import NodeRecord, NodeRegistry


class RouterCore:
    def __init__(
        self,
        persistence: Persistence,
        config: ConfigResolver,
        registration_token: str,
        heartbeat_ttl_sec: float = 15.0,
        library_root: Optional[Path] = None,
        node_timeout_sec: float = 3.0,
    ) -> None:
        self.persistence = persistence
        self.config = config
        self.library_root = library_root
        self.node_timeout_sec = node_timeout_sec
        self.registry = NodeRegistry(
            persistence=persistence,
            registration_token=registration_token,
            heartbeat_ttl_sec=heartbeat_ttl_sec,
        )

    def register_node(self, descriptor: NodeDescriptor, handler: Any) -> Dict[str, Any]:
        result = self.registry.register(descriptor, handler)
        return result

    def heartbeat(self, node_id: str, lease_token: str) -> Dict[str, Any]:
        return self.registry.heartbeat(node_id, lease_token)

    def catalog(self) -> Dict[str, List[Dict[str, Any]]]:
        return self.registry.catalog()

    def registry_snapshot(self) -> Dict[str, Any]:
        return self.registry.snapshot()

    def _node_sort_key(self, rec: NodeRecord) -> Tuple[int, int, int, int, str]:
        major, minor, patch = parse_version(rec.descriptor.node_version)
        return (-rec.descriptor.priority, -major, -minor, -patch, rec.descriptor.node_id)

    def _eligible_nodes(self, intent: str, protocol_version: str) -> List[NodeRecord]:
        nodes: List[NodeRecord] = []
        for rec in self.registry.active_records():
            if protocol_version not in rec.descriptor.supported_protocol_versions:
                continue
            if any(cap.name == intent for cap in rec.descriptor.capabilities):
                nodes.append(rec)
        return nodes

    def _required_extensions_for(self, rec: NodeRecord, intent: str) -> List[str]:
        for cap in rec.descriptor.capabilities:
            if cap.name == intent:
                return list(cap.required_extensions)
        return []

    def _metadata_for(self, rec: NodeRecord, intent: str):
        for cap in rec.descriptor.capabilities:
            if cap.name == intent:
                return cap
        return None

    def _fingerprint_library(self) -> Optional[Tuple[Tuple[str, int, int], ...]]:
        if self.library_root is None or not self.library_root.exists():
            return None
        root = self.library_root.resolve()
        items: List[Tuple[str, int, int]] = []
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            rel = str(path.resolve().relative_to(root)).replace("\\", "/")
            stat = path.stat()
            items.append((rel, int(stat.st_size), int(stat.st_mtime_ns)))
        return tuple(items)

    def _check_confirmation(self, message: Dict[str, Any], approval_required: bool) -> Optional[Dict[str, Any]]:
        if not approval_required:
            return None
        extensions = message.get("extensions", {}) or {}
        confirmation = extensions.get("confirmation") if isinstance(extensions.get("confirmation"), dict) else {}
        if str(confirmation.get("status", "")).lower() != "approved":
            return make_error(
                E_CONFIRMATION_REQUIRED,
                "Approval required before applying changes.",
                message.get("message_id"),
            )
        return None

    def _filter_for_provider(
        self,
        nodes: List[NodeRecord],
        intent: str,
        message: Dict[str, Any],
    ) -> Tuple[List[NodeRecord], Optional[Dict[str, Any]], Optional[Dict[str, str]]]:
        if not intent.startswith("model."):
            return nodes, None, None

        extensions = message.get("extensions", {}) or {}
        llm_ext = extensions.get("llm") if isinstance(extensions.get("llm"), dict) else {}
        selection = self.config.select_llm(llm_ext)

        # Provider/model resolution must be explicit and validated for model intents.
        if selection.provider not in MODEL_PROVIDERS:
            return [], make_error(E_BAD_MESSAGE, "Invalid model provider", message.get("message_id")), None
        if not selection.model:
            return [], make_error(E_BAD_MESSAGE, "Model is required for model intent", message.get("message_id")), None

        requirement_error = self.config.validate_provider_requirements(selection)
        if requirement_error:
            return [], make_error(E_NODE_UNAVAILABLE, requirement_error, message.get("message_id")), None

        filtered: List[NodeRecord] = []
        for rec in nodes:
            cap = self._metadata_for(rec, intent)
            if cap is None:
                continue
            if cap.provider == selection.provider:
                filtered.append(rec)

        if not filtered:
            return [], make_error(
                E_NODE_UNAVAILABLE,
                "Model provider unavailable. Check provider status and config.",
                message.get("message_id"),
                details={"provider": selection.provider, "intent": intent},
            ), None

        disclosure = {
            "provider": selection.provider,
            "model": selection.model,
            "provider_source": selection.provider_source,
            "model_source": selection.model_source,
        }
        return filtered, None, disclosure

    def route(self, message: Dict[str, Any]) -> Dict[str, Any]:
        validation_error = validate_core(message)
        if validation_error:
            return validation_error

        msg_id = message.get("message_id")
        protocol_version = message.get("protocol_version")
        intent = message.get("intent")
        extensions = message.get("extensions", {}) or {}

        if protocol_version != PROTOCOL_VERSION:
            return make_error(
                E_UNSUPPORTED_PROTOCOL,
                f"Protocol version unsupported in this build: {protocol_version}",
                msg_id,
            )

        candidates = self._eligible_nodes(intent, protocol_version)
        if not candidates:
            return make_error(E_NO_ROUTE, f"No matching capability for intent: {intent}", msg_id)

        eligible: List[NodeRecord] = []
        missing_union: List[str] = []
        for rec in candidates:
            missing = [req for req in self._required_extensions_for(rec, intent) if req not in extensions]
            if missing:
                missing_union.extend(missing)
                continue
            eligible.append(rec)

        if not eligible:
            missing_union = sorted(set(missing_union))
            return make_error(
                E_REQUIRED_EXTENSION_MISSING,
                "Required protocol extension is missing for this request.",
                msg_id,
                details={"missing": missing_union},
            )

        protected_meta = self.registry.capability_metadata(intent)
        approval_error = self._check_confirmation(message, bool(protected_meta and protected_meta.approval_required))
        if approval_error:
            return approval_error

        provider_disclosure: Optional[Dict[str, str]] = None
        filtered, provider_error, provider_disclosure = self._filter_for_provider(eligible, intent, message)
        if provider_error:
            return provider_error
        eligible = filtered

        eligible = sorted(eligible, key=self._node_sort_key)

        attempted: List[Dict[str, Any]] = []
        retryable_errors: List[Dict[str, Any]] = []
        for rec in eligible:
            outbound = deepcopy(message)
            ensure_trace(outbound, parent_message_id=msg_id, hop="router.core")
            if provider_disclosure:
                out_ext = outbound.setdefault("extensions", {})
                llm_ext = out_ext.setdefault("llm", {})
                llm_ext["provider"] = provider_disclosure["provider"]
                llm_ext["model"] = provider_disclosure["model"]
                llm_ext["provider_source"] = provider_disclosure["provider_source"]
                llm_ext["model_source"] = provider_disclosure["model_source"]

            self.persistence.emit_event(
                "router",
                "router.route_dispatched",
                {
                    "selected_node_id": rec.descriptor.node_id,
                    "intent": intent,
                },
            )

            started = time.perf_counter()
            try:
                cap_meta = self._metadata_for(rec, intent)
                before_fp = None
                if cap_meta is not None and cap_meta.risk_class == "read" and cap_meta.side_effect_scope == "none":
                    before_fp = self._fingerprint_library()

                if rec.handler is not None:
                    response = rec.handler(outbound)
                else:
                    endpoint = rec.descriptor.endpoint_url
                    if not isinstance(endpoint, str) or not endpoint.startswith("http"):
                        attempted.append({"node_id": rec.descriptor.node_id, "result": "handler_missing"})
                        continue
                    response = http_post_json(endpoint, outbound, timeout_sec=self.node_timeout_sec)

                latency_ms = (time.perf_counter() - started) * 1000.0
                if not looks_like_bdp(response):
                    self.registry.update_health(rec.descriptor.node_id, success=False, latency_ms=None)
                    attempted.append({"node_id": rec.descriptor.node_id, "result": "invalid_response"})
                    continue

                if before_fp is not None:
                    after_fp = self._fingerprint_library()
                    if after_fp is not None and after_fp != before_fp:
                        self.registry.update_health(rec.descriptor.node_id, success=False, latency_ms=None)
                        attempted.append(
                            {
                                "node_id": rec.descriptor.node_id,
                                "result": "undeclared_side_effect",
                            }
                        )
                        continue

                if response.get("intent") == "error":
                    err = response.get("payload", {}).get("error", {})
                    retryable = bool(err.get("retryable", False))
                    if retryable:
                        self.registry.update_health(rec.descriptor.node_id, success=False, latency_ms=None)
                        retryable_errors.append(
                            {
                                "code": err.get("code"),
                                "message": err.get("message"),
                                "details": err.get("details", {}),
                            }
                        )
                        attempted.append({
                            "node_id": rec.descriptor.node_id,
                            "result": "retryable_error",
                            "code": err.get("code"),
                        })
                        continue

                    self.registry.update_health(rec.descriptor.node_id, success=True, latency_ms=latency_ms)
                    return response

                self.registry.update_health(rec.descriptor.node_id, success=True, latency_ms=latency_ms)
                return response
            except Exception as exc:
                self.registry.update_health(rec.descriptor.node_id, success=False, latency_ms=None)
                attempted.append({"node_id": rec.descriptor.node_id, "result": "exception", "error": str(exc)})

        if retryable_errors:
            first = retryable_errors[0]
            return make_error(
                str(first.get("code", E_NODE_UNAVAILABLE)),
                str(first.get("message", "Request failed. You can retry.")),
                msg_id,
                retryable=True,
                details={"attempted": attempted, "upstream": first.get("details", {})},
            )

        if attempted:
            if any(item.get("result") == "undeclared_side_effect" for item in attempted):
                return make_error(
                    E_NODE_ERROR,
                    "Execution failed due to undeclared side effects in read-only capability.",
                    msg_id,
                    retryable=False,
                    details={"attempted": attempted},
                )
            return make_error(
                E_NODE_UNAVAILABLE,
                "No eligible nodes could successfully process the request",
                msg_id,
                retryable=True,
                details={"attempted": attempted},
            )

        return make_error(
            E_INTERNAL,
            "Unexpected internal error. Please retry.",
            msg_id,
            retryable=True,
        )

    def route_for_test(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """Test endpoint equivalent for /intent/test-route style checks."""
        return self.route(message)
