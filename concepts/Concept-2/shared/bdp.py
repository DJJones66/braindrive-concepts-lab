from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from urllib import error, request

PROTOCOL_VERSION = "0.1"

E_BAD_MESSAGE = "E_BAD_MESSAGE"
E_UNSUPPORTED_PROTOCOL = "E_UNSUPPORTED_PROTOCOL"
E_NO_ROUTE = "E_NO_ROUTE"
E_REQUIRED_EXTENSION_MISSING = "E_REQUIRED_EXTENSION_MISSING"
E_AUTHZ_DENIED = "E_AUTHZ_DENIED"
E_POLICY_UNAVAILABLE = "E_POLICY_UNAVAILABLE"
E_CONFIRMATION_REQUIRED = "E_CONFIRMATION_REQUIRED"
E_NODE_UNAVAILABLE = "E_NODE_UNAVAILABLE"
E_NODE_TIMEOUT = "E_NODE_TIMEOUT"
E_NODE_ERROR = "E_NODE_ERROR"
E_NODE_UNTRUSTED = "E_NODE_UNTRUSTED"
E_NODE_NOT_REGISTERED = "E_NODE_NOT_REGISTERED"
E_NODE_REG_INVALID = "E_NODE_REG_INVALID"
E_INTERNAL = "E_INTERNAL"


def new_uuid() -> str:
    return str(uuid.uuid4())


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_extensions(message: Dict[str, Any]) -> Dict[str, Any]:
    if "extensions" not in message or message["extensions"] is None:
        message["extensions"] = {}
    return message


def ensure_trace(message: Dict[str, Any], parent_message_id: Optional[str], hop: Optional[str] = None) -> Dict[str, Any]:
    ensure_extensions(message)
    trace = message["extensions"].setdefault(
        "trace",
        {
            "parent_message_id": parent_message_id or message.get("message_id"),
            "depth": 0,
            "path": [],
        },
    )
    trace.setdefault("parent_message_id", parent_message_id or message.get("message_id"))
    trace.setdefault("depth", 0)
    trace.setdefault("path", [])
    trace["depth"] = int(trace["depth"]) + 1
    if hop:
        trace["path"].append(hop)
    return message


def make_error(
    code: str,
    message: str,
    parent_message_id: Optional[str],
    retryable: bool = False,
    details: Optional[Dict[str, Any]] = None,
    protocol_version: str = PROTOCOL_VERSION,
) -> Dict[str, Any]:
    err: Dict[str, Any] = {
        "protocol_version": protocol_version,
        "message_id": new_uuid(),
        "intent": "error",
        "payload": {
            "error": {
                "code": code,
                "message": message,
                "retryable": retryable,
                "details": details or {},
            }
        },
        "extensions": {},
    }
    if parent_message_id:
        ensure_trace(err, parent_message_id=parent_message_id)
    return err


def make_response(
    intent: str,
    payload: Dict[str, Any],
    parent_message_id: Optional[str],
    extensions: Optional[Dict[str, Any]] = None,
    protocol_version: str = PROTOCOL_VERSION,
) -> Dict[str, Any]:
    response: Dict[str, Any] = {
        "protocol_version": protocol_version,
        "message_id": new_uuid(),
        "intent": intent,
        "payload": payload,
    }
    if extensions:
        response["extensions"] = extensions
    if parent_message_id:
        ensure_trace(response, parent_message_id=parent_message_id)
    return response


def validate_core(message: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(message, dict):
        return make_error(E_BAD_MESSAGE, "Message must be an object", None)

    msg_id = message.get("message_id")
    for field in ("protocol_version", "message_id", "intent", "payload"):
        if field not in message:
            return make_error(E_BAD_MESSAGE, f"Missing required field: {field}", msg_id)

    if not isinstance(message["protocol_version"], str):
        return make_error(E_BAD_MESSAGE, "protocol_version must be string", msg_id)
    if not isinstance(message["message_id"], str):
        return make_error(E_BAD_MESSAGE, "message_id must be string", msg_id)
    if not isinstance(message["intent"], str):
        return make_error(E_BAD_MESSAGE, "intent must be string", msg_id)
    if not isinstance(message["payload"], dict):
        return make_error(E_BAD_MESSAGE, "payload must be object", msg_id)
    if "extensions" in message and message["extensions"] is not None and not isinstance(message["extensions"], dict):
        return make_error(E_BAD_MESSAGE, "extensions must be object if present", msg_id)

    return None


def looks_like_bdp(message: Any) -> bool:
    if not isinstance(message, dict):
        return False
    if not isinstance(message.get("protocol_version"), str):
        return False
    if not isinstance(message.get("message_id"), str):
        return False
    if not isinstance(message.get("intent"), str):
        return False
    if not isinstance(message.get("payload"), dict):
        return False
    if "extensions" in message and message["extensions"] is not None and not isinstance(message["extensions"], dict):
        return False
    return True


def append_jsonl(path: Path, item: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(item, ensure_ascii=True) + "\n")


def _decode_json(raw: bytes, url: str) -> Dict[str, Any]:
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON response from {url}") from exc

    if not isinstance(parsed, dict):
        raise RuntimeError(f"Response from {url} is not a JSON object")
    return parsed


def http_post_json(url: str, payload: Dict[str, Any], timeout_sec: float = 3.0) -> Dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=True).encode("utf-8")
    req = request.Request(url=url, data=data, headers={"Content-Type": "application/json"}, method="POST")

    try:
        with request.urlopen(req, timeout=timeout_sec) as resp:
            raw = resp.read()
    except error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code} from {url}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"HTTP request failed for {url}: {exc}") from exc

    return _decode_json(raw, url)


def http_get_json(url: str, timeout_sec: float = 3.0) -> Dict[str, Any]:
    req = request.Request(url=url, headers={"Accept": "application/json"}, method="GET")
    try:
        with request.urlopen(req, timeout=timeout_sec) as resp:
            raw = resp.read()
    except error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code} from {url}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"HTTP request failed for {url}: {exc}") from exc

    return _decode_json(raw, url)
