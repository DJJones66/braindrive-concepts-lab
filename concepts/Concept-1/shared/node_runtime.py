from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Dict, List, Optional

from shared.bdp import http_post_json


def _parse_capabilities(raw: str) -> List[Dict[str, Any]]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("NODE_CAPABILITIES_JSON must be valid JSON") from exc

    if not isinstance(parsed, list):
        raise ValueError("NODE_CAPABILITIES_JSON must be a JSON array")

    capabilities: List[Dict[str, Any]] = []
    for item in parsed:
        if isinstance(item, str):
            capabilities.append({"name": item, "required_extensions": []})
            continue
        if not isinstance(item, dict):
            raise ValueError("Capability entries must be strings or objects")
        name = str(item.get("name", "")).strip()
        if not name:
            raise ValueError("Capability object missing 'name'")
        required = item.get("required_extensions", [])
        if not isinstance(required, list) or not all(isinstance(v, str) for v in required):
            raise ValueError("required_extensions must be a list of strings")
        capabilities.append({"name": name, "required_extensions": required})

    if not capabilities:
        raise ValueError("At least one capability must be declared")
    return capabilities


def load_descriptor_from_env() -> Dict[str, Any]:
    node_id = os.getenv("NODE_ID", "").strip()
    if not node_id:
        raise ValueError("NODE_ID is required")

    endpoint_url = os.getenv("NODE_ENDPOINT_URL", "").strip()
    if not endpoint_url:
        raise ValueError("NODE_ENDPOINT_URL is required")

    protocols = [v.strip() for v in os.getenv("NODE_SUPPORTED_PROTOCOLS", "0.1").split(",") if v.strip()]
    capabilities = _parse_capabilities(os.getenv("NODE_CAPABILITIES_JSON", "[]"))

    return {
        "node_id": node_id,
        "node_version": os.getenv("NODE_VERSION", "0.1.0"),
        "endpoint_url": endpoint_url,
        "supported_protocol_versions": protocols,
        "capabilities": capabilities,
        "priority": int(os.getenv("NODE_PRIORITY", "100")),
        "region": os.getenv("NODE_REGION", "local"),
        "cost_class": os.getenv("NODE_COST_CLASS", "standard"),
        "auth": {
            "registration_token": os.getenv("ROUTER_REGISTRATION_TOKEN", "concept1-dev-token"),
        },
    }


def start_registration_loop(stop_event: Optional[threading.Event] = None) -> threading.Thread:
    register_url = os.getenv("ROUTER_REGISTER_URL", "http://node-router:8080/router/node/register")
    heartbeat_url = os.getenv("ROUTER_HEARTBEAT_URL", "http://node-router:8080/router/node/heartbeat")
    heartbeat_sec = float(os.getenv("ROUTER_HEARTBEAT_SEC", "5.0"))
    register_retry_sec = float(os.getenv("ROUTER_REGISTER_RETRY_SEC", "2.0"))
    descriptor = load_descriptor_from_env()

    state = {"lease_token": None}
    local_stop = stop_event or threading.Event()

    def _loop() -> None:
        while not local_stop.is_set():
            try:
                if not state["lease_token"]:
                    reg_resp = http_post_json(register_url, descriptor, timeout_sec=2.5)
                    state["lease_token"] = reg_resp.get("lease_token")
                    if not state["lease_token"]:
                        raise RuntimeError("register response missing lease_token")
                    print(f"[{descriptor['node_id']}] registered with router")

                hb_resp = http_post_json(
                    heartbeat_url,
                    {"node_id": descriptor["node_id"], "lease_token": state["lease_token"]},
                    timeout_sec=2.5,
                )
                if not hb_resp.get("ok", False):
                    raise RuntimeError("heartbeat rejected")
                time.sleep(heartbeat_sec)
            except Exception as exc:
                state["lease_token"] = None
                print(f"[{descriptor['node_id']}] registration/heartbeat issue: {exc}")
                time.sleep(register_retry_sec)

    thread = threading.Thread(target=_loop, daemon=True, name=f"register-{descriptor['node_id']}")
    thread.start()
    return thread
