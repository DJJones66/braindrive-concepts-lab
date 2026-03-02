#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict
from urllib import error, request

def _env(*names: str, default: str) -> str:
    for name in names:
        value = os.getenv(name)
        if value is not None:
            return value
    return default


ROUTER_BASE = _env("BRAINDRIVE_ROUTER_BASE", default="http://localhost:9480")
INTENT_BASE = _env("BRAINDRIVE_INTENT_BASE", default="http://localhost:9481")
TIMEOUT_SEC = float(_env("BRAINDRIVE_DEMO_TIMEOUT_SEC", default="4.0"))


def _request(method: str, url: str, payload: Dict[str, Any] | None = None) -> str:
    body = None
    headers = {"Accept": "application/json"}
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
        raise RuntimeError(f"response from {url} is not JSON object")
    return parsed


def post_json(url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    raw = _request("POST", url, payload)
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise RuntimeError(f"response from {url} is not JSON object")
    return parsed


def wait_for(url: str, label: str, attempts: int = 60, sleep_sec: float = 1.0) -> None:
    for _ in range(attempts):
        try:
            body = get_json(url)
            if body.get("ok"):
                print(f"[ok] {label} healthy")
                return
        except Exception:
            pass
        time.sleep(sleep_sec)
    raise RuntimeError(f"timed out waiting for {label}: {url}")


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> None:
    print("Using endpoints:")
    print(f"  router: {ROUTER_BASE}")
    print(f"  intent: {INTENT_BASE}")

    wait_for(f"{ROUTER_BASE}/health", "router.core")
    wait_for(f"{INTENT_BASE}/health", "intent.router.natural-language")

    registry = get_json(f"{ROUTER_BASE}/router/registry")
    nodes = registry.get("nodes", [])
    assert_true(isinstance(nodes, list) and len(nodes) >= 10, "expected at least 10 registered nodes")
    print(f"[ok] dynamic registration active with {len(nodes)} nodes")

    route_create = post_json(
        f"{INTENT_BASE}/intent/route",
        {"message": "Create folder for my finances", "confirm": True},
    )
    assert_true(route_create.get("status") == "routed", "folder create should route")
    assert_true(route_create.get("route_response", {}).get("intent") == "folder.created", "folder.create should execute")
    print("[ok] folder.create routed and executed")

    route_switch = post_json(
        f"{INTENT_BASE}/intent/route",
        {"message": "switch folder to my finances"},
    )
    assert_true(route_switch.get("status") == "routed", "folder switch should route")
    assert_true(route_switch.get("route_response", {}).get("intent") == "folder.switched", "folder.switch should execute")
    print("[ok] folder.switch routed and executed")

    model_catalog = post_json(
        f"{INTENT_BASE}/intent/route",
        {"message": "list models"},
    )
    assert_true(model_catalog.get("status") in {"routed", "route_error"}, "model catalog route response expected")
    print("[ok] model intent route path exercised")

    mutation_blocked = post_json(
        f"{INTENT_BASE}/intent/route",
        {"message": "write file notes.md with hello"},
    )
    assert_true(mutation_blocked.get("status") == "route_error", "mutation without approval should fail-closed")
    err = mutation_blocked.get("route_response", {}).get("payload", {}).get("error", {})
    assert_true(err.get("code") == "E_CONFIRMATION_REQUIRED", "expected E_CONFIRMATION_REQUIRED")
    print("[ok] protected mutation is fail-closed without approval")

    print("Concept-4 docker compose demo checks passed.")


if __name__ == "__main__":
    main()
