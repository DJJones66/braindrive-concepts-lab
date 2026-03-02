#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict
from urllib import error, request

ROUTER_BASE = os.getenv("CONCEPT2_ROUTER_BASE", "http://localhost:9280")
INTENT_BASE = os.getenv("CONCEPT2_INTENT_BASE", "http://localhost:9281")
AUTH_BASE = os.getenv("CONCEPT2_AUTH_BASE", "http://localhost:9282")
TIMEOUT_SEC = float(os.getenv("CONCEPT2_DEMO_TIMEOUT_SEC", "3.0"))


def _request(method: str, url: str, payload: Dict[str, Any] | None = None, accept_json: bool = True) -> str:
    body = None
    headers = {"Accept": "application/json" if accept_json else "*/*"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = request.Request(url=url, data=body, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=TIMEOUT_SEC) as resp:
            return resp.read().decode("utf-8")
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} for {url}: {raw}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"request failed for {url}: {exc}") from exc


def get_json(url: str) -> Dict[str, Any]:
    raw = _request("GET", url)
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise RuntimeError(f"response from {url} is not a JSON object")
    return parsed


def post_json(url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    raw = _request("POST", url, payload)
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise RuntimeError(f"response from {url} is not a JSON object")
    return parsed


def get_text(url: str) -> str:
    return _request("GET", url, accept_json=False)


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
    assert_true(isinstance(nodes, list) and len(nodes) >= 8, "expected at least 8 registered nodes")
    print(f"[ok] dynamic registration: {len(nodes)} nodes active")

    capabilities = get_json(f"{INTENT_BASE}/intent/capabilities")
    fake_caps = capabilities.get("fake_capabilities", [])
    assert_true(any(item.get("capability") == "md.library.create_note" for item in fake_caps), "missing fake markdown capabilities")
    print("[ok] fake capability catalog is visible")

    ui_html = get_text(f"{INTENT_BASE}/ui")
    assert_true("Concept-2 Intent Lab" in ui_html, "ui page content mismatch")
    print("[ok] intent lab UI is served")

    common_identity = {
        "actor_id": "user.demo",
        "actor_type": "human",
        "roles": ["admin", "user"],
    }
    common_authz = {
        "decision": "allow",
        "decision_id": "demo-authz-2",
    }

    analyze_1 = post_json(
        f"{INTENT_BASE}/intent/analyze",
        {
            "message": "Search notes for roadmap",
            "conversation_id": "demo-conv-1",
            "identity": common_identity,
            "authz": common_authz,
        },
    )
    assert_true(analyze_1.get("status") == "analyzed", "analyze status should be analyzed")
    assert_true(
        analyze_1.get("analysis", {}).get("canonical_intent") == "md.library.search_notes",
        "analyze should map to md.library.search_notes",
    )
    print("[ok] /intent/analyze returns intent + confidence data")

    note_title = f"demo-note-{int(time.time())}"

    scenario_create_a = post_json(
        f"{INTENT_BASE}/intent/route",
        {
            "message": f"Create note called {note_title} with kickoff checklist",
            "conversation_id": "demo-conv-1",
            "identity": common_identity,
            "authz": common_authz,
        },
    )
    assert_true(scenario_create_a.get("status") == "needs_confirmation", "create note should require confirmation")
    print("[ok] create-note intent is confirmation-gated")

    scenario_create_b = post_json(
        f"{INTENT_BASE}/intent/route",
        {
            "message": f"Create note called {note_title} with kickoff checklist",
            "conversation_id": "demo-conv-1",
            "identity": common_identity,
            "authz": common_authz,
            "confirm": True,
        },
    )
    response_create_b = scenario_create_b.get("route_response", {})
    assert_true(response_create_b.get("intent") == "md.library.note_created", "create note should execute")
    note_id = response_create_b.get("payload", {}).get("note_id", "")
    assert_true(bool(note_id), "create note response missing note_id")
    print("[ok] create-note execution succeeded")

    scenario_append = post_json(
        f"{INTENT_BASE}/intent/route",
        {
            "message": f"Append to note {note_id} with add release checklist",
            "conversation_id": "demo-conv-1",
            "identity": common_identity,
            "authz": common_authz,
            "confirm": True,
        },
    )
    response_append = scenario_append.get("route_response", {})
    assert_true(response_append.get("intent") == "md.library.note_appended", "append note should execute")
    print("[ok] append-note execution succeeded")

    scenario_read = post_json(
        f"{INTENT_BASE}/intent/route",
        {
            "message": f"Read note {note_id}",
            "conversation_id": "demo-conv-1",
            "identity": common_identity,
            "authz": common_authz,
        },
    )
    response_read = scenario_read.get("route_response", {})
    assert_true(response_read.get("intent") == "md.library.note_read", "read note should execute")
    content = response_read.get("payload", {}).get("content", "")
    assert_true("release checklist" in content.lower(), "appended content missing in read output")
    print("[ok] read-note execution succeeded")

    _ = post_json(f"{AUTH_BASE}/mode", {"mode": "down"})
    try:
        scenario_policy = post_json(
            f"{INTENT_BASE}/intent/route",
            {
                "message": f"Create note called blocked-{note_title}",
                "conversation_id": "demo-conv-1",
                "identity": common_identity,
                "authz": common_authz,
                "confirm": True,
            },
        )
        response_policy = scenario_policy.get("route_response", {})
        assert_true(
            response_policy.get("intent") == "error"
            and response_policy.get("payload", {}).get("error", {}).get("code") == "E_POLICY_UNAVAILABLE",
            "expected fail-closed policy behavior",
        )
        print("[ok] fail-closed policy behavior still enforced")
    finally:
        _ = post_json(f"{AUTH_BASE}/mode", {"mode": "allow"})

    print("All Concept-2 demo scenarios passed.")


if __name__ == "__main__":
    main()
