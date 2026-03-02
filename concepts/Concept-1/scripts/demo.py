#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict
from urllib import error, request

ROUTER_BASE = os.getenv("CONCEPT1_ROUTER_BASE", "http://localhost:9180")
INTENT_BASE = os.getenv("CONCEPT1_INTENT_BASE", "http://localhost:9181")
AUTH_BASE = os.getenv("CONCEPT1_AUTH_BASE", "http://localhost:9182")
TIMEOUT_SEC = float(os.getenv("CONCEPT1_DEMO_TIMEOUT_SEC", "3.0"))


def _request_json(method: str, url: str, payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = request.Request(url=url, data=body, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=TIMEOUT_SEC) as resp:
            raw = resp.read().decode("utf-8")
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} for {url}: {raw}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"request failed for {url}: {exc}") from exc

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"non-JSON response from {url}: {raw}") from exc

    if not isinstance(parsed, dict):
        raise RuntimeError(f"response from {url} is not a JSON object")
    return parsed


def get_json(url: str) -> Dict[str, Any]:
    return _request_json("GET", url)


def post_json(url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    return _request_json("POST", url, payload)


def wait_for(url: str, label: str, attempts: int = 40, sleep_sec: float = 1.0) -> None:
    for _ in range(attempts):
        try:
            body = get_json(url)
            if body.get("ok"):
                print(f"[ok] {label} healthy")
                return
        except Exception:
            pass
        time.sleep(sleep_sec)
    raise RuntimeError(f"timed out waiting for {label} health endpoint: {url}")


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> None:
    print("Using endpoints:")
    print(f"  router: {ROUTER_BASE}")
    print(f"  intent: {INTENT_BASE}")
    print(f"  auth:   {AUTH_BASE}")

    wait_for(f"{ROUTER_BASE}/health", "node.router")
    wait_for(f"{INTENT_BASE}/health", "intent.router.natural-language")
    wait_for(f"{AUTH_BASE}/health", "node.auth.policy")

    registry = get_json(f"{ROUTER_BASE}/router/registry")
    nodes = registry.get("nodes", [])
    assert_true(isinstance(nodes, list) and len(nodes) >= 7, "expected at least 7 registered nodes")
    print(f"[ok] dynamic registration: {len(nodes)} nodes active")

    common_identity = {
        "actor_id": "user.demo",
        "actor_type": "human",
        "roles": ["admin", "user"],
    }
    common_authz = {
        "decision": "allow",
        "decision_id": "demo-authz-1",
    }

    scenario_1 = post_json(
        f"{INTENT_BASE}/intent/route",
        {
            "message": "Explain routing decisions in one sentence.",
            "conversation_id": "demo-conv-1",
            "identity": common_identity,
            "authz": common_authz,
        },
    )
    assert_true(scenario_1.get("status") == "routed", "scenario 1 expected routed status")
    response_1 = scenario_1.get("route_response", {})
    assert_true(response_1.get("intent") == "chat.response", "scenario 1 expected chat.response")
    handled_by_1 = response_1.get("payload", {}).get("handled_by", "")
    assert_true("primary" in handled_by_1, "scenario 1 should route to primary chat node")
    print("[ok] scenario 1: normal chat route to primary node")

    scenario_2 = post_json(
        f"{INTENT_BASE}/intent/route",
        {
            "message": "Please answer this [fail-primary] to test fallback.",
            "conversation_id": "demo-conv-1",
            "identity": common_identity,
            "authz": common_authz,
        },
    )
    assert_true(scenario_2.get("status") == "routed", "scenario 2 expected routed status")
    response_2 = scenario_2.get("route_response", {})
    handled_by_2 = response_2.get("payload", {}).get("handled_by", "")
    assert_true("backup" in handled_by_2, "scenario 2 should failover to backup chat node")
    print("[ok] scenario 2: retry/fallback to backup node")

    scenario_3 = post_json(
        f"{INTENT_BASE}/intent/route",
        {
            "message": "Create a new page for Q2 planning",
            "conversation_id": "demo-conv-1",
            "identity": common_identity,
            "authz": common_authz,
        },
    )
    assert_true(
        scenario_3.get("status") == "needs_confirmation",
        "scenario 3 expected confirmation requirement",
    )
    print("[ok] scenario 3: mutation request correctly requires confirmation")

    scenario_4 = post_json(
        f"{INTENT_BASE}/intent/route",
        {
            "message": "Create a new page for Q2 planning",
            "conversation_id": "demo-conv-1",
            "identity": common_identity,
            "authz": common_authz,
            "confirm": True,
        },
    )
    assert_true(scenario_4.get("status") == "routed", "scenario 4 expected routed status")
    response_4 = scenario_4.get("route_response", {})
    assert_true(response_4.get("intent") == "workflow.page.created", "scenario 4 expected workflow.page.created")
    print("[ok] scenario 4: confirmed mutation routed and executed")

    _ = post_json(f"{AUTH_BASE}/mode", {"mode": "down"})
    try:
        scenario_5 = post_json(
            f"{INTENT_BASE}/intent/route",
            {
                "message": "Create a new page for policy outage test",
                "conversation_id": "demo-conv-1",
                "identity": common_identity,
                "authz": common_authz,
                "confirm": True,
            },
        )
        response_5 = scenario_5.get("route_response", {})
        assert_true(
            response_5.get("intent") == "error"
            and response_5.get("payload", {}).get("error", {}).get("code") == "E_POLICY_UNAVAILABLE",
            "scenario 5 expected fail-closed policy denial",
        )
        print("[ok] scenario 5: fail-closed policy behavior under auth outage")
    finally:
        _ = post_json(f"{AUTH_BASE}/mode", {"mode": "allow"})

    scenario_6a = post_json(
        f"{INTENT_BASE}/intent/route",
        {
            "message": "Delete that old draft",
            "conversation_id": "demo-conv-1",
            "identity": common_identity,
            "authz": common_authz,
        },
    )
    assert_true(scenario_6a.get("status") == "needs_confirmation", "scenario 6a expected confirmation")

    scenario_6b = post_json(
        f"{INTENT_BASE}/intent/route",
        {
            "message": "Delete that old draft",
            "conversation_id": "demo-conv-1",
            "identity": common_identity,
            "authz": common_authz,
            "confirm": True,
        },
    )
    response_6b = scenario_6b.get("route_response", {})
    assert_true(response_6b.get("intent") == "memory.deleted", "scenario 6b expected memory.deleted")
    print("[ok] scenario 6: destructive action requires then honors confirmation")

    print("All Concept-1 demo scenarios passed.")


if __name__ == "__main__":
    main()
