from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from urllib import error, request

from .constants import (
    E_BAD_MESSAGE,
    PROTOCOL_VERSION,
)

Message = Dict[str, Any]


def new_uuid() -> str:
    return str(uuid.uuid4())


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_extensions(message: Message) -> Message:
    if "extensions" not in message or message["extensions"] is None:
        message["extensions"] = {}
    return message


def ensure_trace(message: Message, parent_message_id: Optional[str], hop: Optional[str] = None) -> Message:
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


def make_response(
    intent: str,
    payload: Dict[str, Any],
    parent_message_id: Optional[str],
    extensions: Optional[Dict[str, Any]] = None,
    protocol_version: str = PROTOCOL_VERSION,
) -> Message:
    response: Message = {
        "protocol_version": protocol_version,
        "message_id": new_uuid(),
        "intent": intent,
        "payload": payload,
    }
    if extensions:
        response["extensions"] = extensions
    if parent_message_id:
        ensure_trace(response, parent_message_id)
    return response


def make_error(
    code: str,
    message: str,
    parent_message_id: Optional[str],
    retryable: bool = False,
    details: Optional[Dict[str, Any]] = None,
    protocol_version: str = PROTOCOL_VERSION,
) -> Message:
    err = {
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
        ensure_trace(err, parent_message_id)
    return err


def validate_core(message: Any) -> Optional[Message]:
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
    return validate_core(message) is None


def _decode_json(raw: bytes, url: str) -> Dict[str, Any]:
    import json

    try:
        parsed = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON response from {url}") from exc

    if not isinstance(parsed, dict):
        raise RuntimeError(f"Response from {url} is not a JSON object")
    return parsed


def http_post_json(url: str, payload: Dict[str, Any], timeout_sec: float = 3.0) -> Dict[str, Any]:
    import json

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
