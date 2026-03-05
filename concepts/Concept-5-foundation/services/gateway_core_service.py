#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from braindrive_runtime.protocol import http_post_json, new_uuid, now_iso

CORE_CONTRACT_VERSION = "gateway-core-contract/v1"
CANONICAL_STREAM_EVENT_TYPES = {
    "metadata",
    "delta",
    "approval_required",
    "complete",
    "error",
}


PersistFn = Callable[[], None]
AppendLogFn = Callable[[str, Dict[str, Any]], None]
HttpPostFn = Callable[[str, Dict[str, Any], float], Dict[str, Any]]


def default_core_state() -> Dict[str, Any]:
    return {
        "conversations": {},
        "console_sessions": {},
        "stream_queues": {},
    }


def ensure_core_state(state: Dict[str, Any]) -> None:
    for key, fallback in default_core_state().items():
        if not isinstance(state.get(key), type(fallback)):
            state[key] = fallback


def validate_core_request_envelope(request: Dict[str, Any], *, strict: bool = False) -> List[str]:
    errors: List[str] = []

    if not isinstance(request, dict):
        return ["request must be an object"]

    required = {"request_id", "auth_context"}
    missing = [key for key in required if key not in request]
    if missing:
        errors.append(f"missing required fields: {sorted(missing)}")

    request_id = str(request.get("request_id", "")).strip()
    if not request_id:
        errors.append("request_id is required")

    auth_context = request.get("auth_context", {})
    if not isinstance(auth_context, dict):
        errors.append("auth_context must be an object")
        return errors

    actor_id = str(auth_context.get("actor_id", "")).strip()
    if not actor_id:
        errors.append("auth_context.actor_id is required")

    roles = auth_context.get("roles", [])
    if not isinstance(roles, list):
        errors.append("auth_context.roles must be a list")

    scopes = auth_context.get("scopes", [])
    if not isinstance(scopes, list):
        errors.append("auth_context.scopes must be a list")

    if strict:
        allowed = {
            "request_id",
            "conversation_id",
            "auth_context",
            "message",
            "context",
            "metadata",
            "confirm",
            "approval_request_id",
            "adapter_contract_version",
            "core_contract_version",
        }
        unknown = sorted(set(request.keys()) - allowed)
        if unknown:
            errors.append(f"unknown fields in strict mode: {unknown}")

    return errors


def validate_core_response_envelope(response: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    if not isinstance(response, dict):
        return ["response must be an object"]

    if not isinstance(response.get("ok"), bool):
        errors.append("response.ok must be boolean")

    conversation_id = str(response.get("conversation_id", "")).strip()
    if not conversation_id:
        errors.append("response.conversation_id is required")

    if response.get("ok") is True:
        record = response.get("message_record", {})
        if record and not isinstance(record, dict):
            errors.append("response.message_record must be an object")

    return errors


def validate_stream_event_envelope(event: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    if not isinstance(event, dict):
        return ["event must be an object"]

    event_name = str(event.get("event", "")).strip()
    if not event_name:
        errors.append("event.event is required")
    elif event_name in CANONICAL_STREAM_EVENT_TYPES:
        pass
    elif event_name.startswith("terminal."):
        # Backward-compatible console stream passthrough from core to adapter.
        pass
    else:
        errors.append(f"event.event must be canonical or terminal.*, got: {event_name}")

    payload = event.get("payload", {})
    if not isinstance(payload, dict):
        errors.append("event.payload must be an object")

    return errors


def get_or_create_conversation(
    *,
    state: Dict[str, Any],
    conversation_id: str,
    auth_context: Dict[str, Any],
    metadata: Dict[str, Any],
) -> Dict[str, Any]:
    ensure_core_state(state)
    conversations = state.setdefault("conversations", {})
    convo = conversations.get(conversation_id)
    if not isinstance(convo, dict):
        convo = {
            "conversation_id": conversation_id,
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "actor_id": str(auth_context.get("actor_id", "")),
            "metadata": metadata,
            "messages": [],
        }
        conversations[conversation_id] = convo
    else:
        convo["updated_at"] = now_iso()
        if metadata:
            convo["metadata"] = metadata
    return convo


def append_conversation_record(
    *,
    state: Dict[str, Any],
    persist_state: PersistFn,
    append_log: AppendLogFn,
    conversation_id: str,
    message: str,
    result: Dict[str, Any],
    auth_context: Dict[str, Any],
    metadata: Dict[str, Any],
) -> Dict[str, Any]:
    ensure_core_state(state)
    record_id = f"msg_{new_uuid()}"
    record = {
        "record_id": record_id,
        "conversation_id": conversation_id,
        "created_at": now_iso(),
        "actor_id": str(auth_context.get("actor_id", "")),
        "actor_type": str(auth_context.get("actor_type", "")),
        "roles": auth_context.get("roles", []),
        "status": str(result.get("status", "")),
        "message": message,
        "route_intent": str((result.get("route_response", {}) or {}).get("intent", "")),
        "metadata": metadata,
    }

    convo = get_or_create_conversation(
        state=state,
        conversation_id=conversation_id,
        auth_context=auth_context,
        metadata=metadata,
    )
    messages = convo.get("messages", [])
    if not isinstance(messages, list):
        messages = []
    messages.append(record)
    convo["messages"] = messages[-200:]
    convo["updated_at"] = now_iso()
    state.setdefault("conversations", {})[conversation_id] = convo
    persist_state()

    append_log(
        "gateway_messages",
        {
            "timestamp": now_iso(),
            "conversation_id": conversation_id,
            "record": record,
            "route_response": result.get("route_response", {}),
        },
    )

    return record


def enqueue_stream_event(
    *,
    state: Dict[str, Any],
    persist_state: PersistFn,
    conversation_id: str,
    event_type: str,
    payload: Dict[str, Any],
) -> None:
    ensure_core_state(state)
    queues = state.setdefault("stream_queues", {})
    queue = queues.get(conversation_id)
    if not isinstance(queue, list):
        queue = []
    queue.append({"event": event_type, "payload": payload, "ts": now_iso()})
    queues[conversation_id] = queue[-500:]
    persist_state()


def pop_stream_events(
    *,
    state: Dict[str, Any],
    persist_state: PersistFn,
    conversation_id: str,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    ensure_core_state(state)
    queues = state.setdefault("stream_queues", {})
    queue = queues.get(conversation_id)
    if not isinstance(queue, list) or not queue:
        return []
    events = queue[: max(1, limit)]
    queues[conversation_id] = queue[max(1, limit) :]
    persist_state()
    return events


def route_text_preview(route_response: Dict[str, Any]) -> str:
    intent = str(route_response.get("intent", ""))
    payload = route_response.get("payload", {}) if isinstance(route_response.get("payload", {}), dict) else {}
    if intent in {"chat.response", "model.chat.completed"}:
        return str(payload.get("text", "")).strip()
    if intent == "web.scrape.completed":
        results = payload.get("results", [])
        if isinstance(results, list) and results:
            first = results[0] if isinstance(results[0], dict) else {}
            content = first.get("content", [])
            if isinstance(content, list) and content:
                return str(content[0]).strip()
    return ""


def _safe_conversation_filename(conversation_id: str) -> str:
    raw = str(conversation_id).strip()
    safe = "".join(ch for ch in raw if ch.isalnum() or ch in {"-", "_"})
    return safe or f"conv_{new_uuid()}"


def _chat_paths(library_root: str, conversation_id: str) -> tuple[Path, Path]:
    root = Path(library_root).resolve()
    chats_dir = root / "chats"
    chats_dir.mkdir(parents=True, exist_ok=True)
    name = _safe_conversation_filename(conversation_id)
    return chats_dir / f"{name}.jsonl", chats_dir / f"{name}.meta.json"


def _append_chat_record(
    *,
    library_root: str,
    conversation_id: str,
    record: Dict[str, Any],
    write_sidecar: bool,
) -> None:
    root = str(library_root).strip()
    if not root:
        return
    jsonl_path, meta_path = _chat_paths(root, conversation_id)

    with jsonl_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=True) + "\n")

    if not write_sidecar:
        return

    sidecar: Dict[str, Any] = {}
    if meta_path.exists():
        try:
            loaded = json.loads(meta_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                sidecar = loaded
        except Exception:
            sidecar = {}

    sidecar["conversation_id"] = conversation_id
    sidecar["record_count"] = int(sidecar.get("record_count", 0)) + 1
    sidecar["updated_at"] = str(record.get("ts", now_iso()))
    sidecar["last_record_id"] = str(record.get("record_id", ""))
    meta_path.write_text(json.dumps(sidecar, ensure_ascii=True, indent=2), encoding="utf-8")


def _load_provider_history_messages(
    *,
    library_root: str,
    conversation_id: str,
    max_turns: int,
    max_chars: int,
) -> List[Dict[str, str]]:
    root = str(library_root).strip()
    if not root:
        return []
    jsonl_path, _ = _chat_paths(root, conversation_id)
    if not jsonl_path.exists():
        return []

    messages: List[Dict[str, str]] = []
    try:
        for line in jsonl_path.read_text(encoding="utf-8").splitlines():
            item = json.loads(line)
            if not isinstance(item, dict):
                continue
            input_obj = item.get("input", {})
            output_obj = item.get("output", {})
            user_text = str(input_obj.get("text", "")).strip() if isinstance(input_obj, dict) else ""
            assistant_text = str(output_obj.get("text", "")).strip() if isinstance(output_obj, dict) else ""
            if user_text:
                messages.append({"role": "user", "content": user_text})
            if assistant_text:
                messages.append({"role": "assistant", "content": assistant_text})
    except Exception:
        return []

    if max_turns > 0:
        messages = messages[-(max_turns * 2) :]
    if max_chars <= 0:
        return messages

    bounded: List[Dict[str, str]] = []
    used = 0
    for item in reversed(messages):
        content = str(item.get("content", ""))
        if not content:
            continue
        if used + len(content) > max_chars:
            break
        bounded.append(item)
        used += len(content)
    bounded.reverse()
    return bounded


def route_nl_message(
    *,
    state: Dict[str, Any],
    persist_state: PersistFn,
    append_log: AppendLogFn,
    intent_router_base_url: str,
    http_timeout_sec: float,
    body: Dict[str, Any],
    auth_context: Dict[str, Any],
    conversation_id: str,
    library_root: str = "",
    provider_context_enabled: bool = True,
    provider_context_max_turns: int = 12,
    provider_context_max_chars: int = 12000,
    chat_sidecar_enabled: bool = True,
    post_json: HttpPostFn = http_post_json,
) -> Dict[str, Any]:
    message = str(body.get("message", "")).strip()
    if not message:
        return {
            "ok": False,
            "error": {"code": "E_BAD_MESSAGE", "message": "message is required"},
            "conversation_id": conversation_id,
        }

    context = body.get("context", {})
    if not isinstance(context, dict):
        context = {}

    metadata = body.get("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}

    confirm = bool(body.get("confirm", False))
    extensions = body.get("extensions", {})
    if not isinstance(extensions, dict):
        extensions = {}

    extensions["identity"] = {
        "actor_id": str(auth_context.get("actor_id", "")),
        "roles": auth_context.get("roles", []),
        "actor_type": str(auth_context.get("actor_type", "")),
        "scopes": auth_context.get("scopes", []),
    }
    extensions["trace"] = {
        "trace_id": str(auth_context.get("trace_id", "")),
        "auth_session_id": str(auth_context.get("auth_session_id", "")),
        "conversation_id": conversation_id,
    }

    request_context = dict(context)
    if provider_context_enabled:
        history_messages = _load_provider_history_messages(
            library_root=library_root,
            conversation_id=conversation_id,
            max_turns=max(1, int(provider_context_max_turns)),
            max_chars=max(1, int(provider_context_max_chars)),
        )
        if history_messages:
            request_context["provider_history_messages"] = history_messages

    request_payload = {
        "message": message,
        "confirm": confirm,
        "context": request_context,
        "extensions": extensions,
    }

    try:
        routed = post_json(
            f"{intent_router_base_url}/intent/route",
            request_payload,
            timeout_sec=http_timeout_sec,
        )
    except Exception as exc:
        return {
            "ok": False,
            "error": {
                "code": "E_NODE_UNAVAILABLE",
                "message": f"intent-router call failed: {exc}",
            },
            "conversation_id": conversation_id,
        }

    if not isinstance(routed, dict):
        return {
            "ok": False,
            "error": {
                "code": "E_NODE_ERROR",
                "message": "intent-router returned invalid response",
            },
            "conversation_id": conversation_id,
        }

    record = append_conversation_record(
        state=state,
        persist_state=persist_state,
        append_log=append_log,
        conversation_id=conversation_id,
        message=message,
        result=routed,
        auth_context=auth_context,
        metadata=metadata,
    )

    result = {
        "ok": True,
        "conversation_id": conversation_id,
        "auth_context": auth_context,
        "message_record": record,
        "status": routed.get("status", ""),
        "analysis": routed.get("analysis", {}),
        "route_message": routed.get("route_message", {}),
        "route_response": routed.get("route_response", {}),
    }

    enqueue_stream_event(
        state=state,
        persist_state=persist_state,
        conversation_id=conversation_id,
        event_type="metadata",
        payload={
            "conversation_id": conversation_id,
            "record_id": str(record.get("record_id", "")),
            "status": result.get("status", ""),
        },
    )
    preview = route_text_preview(result.get("route_response", {}))
    if preview:
        enqueue_stream_event(
            state=state,
            persist_state=persist_state,
            conversation_id=conversation_id,
            event_type="delta",
            payload={"text": preview},
        )
    enqueue_stream_event(
        state=state,
        persist_state=persist_state,
        conversation_id=conversation_id,
        event_type="complete",
        payload=result,
    )

    output_intent = str((result.get("route_response", {}) if isinstance(result.get("route_response", {}), dict) else {}).get("intent", ""))
    output_text = route_text_preview(result.get("route_response", {}))
    console_session_id = str(context.get("console_session_id", "")).strip()
    if not console_session_id and isinstance(metadata, dict):
        console_session_id = str(metadata.get("console_session_id", "")).strip()
    chat_record = {
        "ts": now_iso(),
        "conversation_id": conversation_id,
        "record_id": str(record.get("record_id", "")),
        "actor": {
            "id": str(auth_context.get("actor_id", "")),
            "type": str(auth_context.get("actor_type", "user") or "user"),
        },
        "channel": str(metadata.get("channel", "") if isinstance(metadata, dict) else ""),
        "route": {
            "intent": str(record.get("route_intent", "")),
            "status": str(record.get("status", "")),
        },
        "input": {"text": message},
        "output": {"intent": output_intent, "text": output_text},
        "metadata": metadata if isinstance(metadata, dict) else {},
        "trace": {
            "trace_id": str(auth_context.get("trace_id", "")),
            "auth_session_id": str(auth_context.get("auth_session_id", "")),
            "console_session_id": console_session_id,
        },
    }
    try:
        _append_chat_record(
            library_root=library_root,
            conversation_id=conversation_id,
            record=chat_record,
            write_sidecar=chat_sidecar_enabled,
        )
    except Exception as exc:
        # Keep routing non-fatal even when durable chat persistence cannot be written,
        # but emit an operational breadcrumb for debugging deployment issues.
        try:
            append_log(
                "gateway_chat_persistence",
                {
                    "timestamp": now_iso(),
                    "conversation_id": conversation_id,
                    "record_id": str(record.get("record_id", "")),
                    "library_root": str(library_root),
                    "error": str(exc),
                },
            )
        except Exception:
            pass

    return result


def route_bdp(
    *,
    router_base_url: str,
    http_timeout_sec: float,
    intent: str,
    payload: Dict[str, Any],
    auth_context: Dict[str, Any],
    conversation_id: str = "",
    confirm: bool = False,
    approval_request_id: str = "",
    post_json: HttpPostFn = http_post_json,
) -> Dict[str, Any]:
    extensions: Dict[str, Any] = {
        "identity": {
            "actor_id": str(auth_context.get("actor_id", "")),
            "roles": auth_context.get("roles", []),
            "actor_type": str(auth_context.get("actor_type", "")),
            "scopes": auth_context.get("scopes", []),
        },
        "trace": {
            "trace_id": str(auth_context.get("trace_id", "")),
            "auth_session_id": str(auth_context.get("auth_session_id", "")),
            "conversation_id": conversation_id,
        },
    }

    if confirm and approval_request_id:
        extensions["confirmation"] = {
            "required": True,
            "status": "approved",
            "request_id": approval_request_id,
        }

    message = {
        "protocol_version": "0.1",
        "message_id": str(new_uuid()),
        "intent": intent,
        "payload": payload,
        "extensions": extensions,
    }

    return post_json(f"{router_base_url}/route", message, timeout_sec=http_timeout_sec)


def handle_console_open(
    *,
    state: Dict[str, Any],
    persist_state: PersistFn,
    route_bdp_fn: Callable[..., Dict[str, Any]],
    body: Dict[str, Any],
    auth_context: Dict[str, Any],
    conversation_id: str,
) -> Dict[str, Any]:
    payload = {
        "origin": str(body.get("origin", "")).strip(),
        "target": str(body.get("target", "")).strip(),
        "source_ip": str(body.get("source_ip", "")).strip(),
    }
    result = route_bdp_fn(
        intent="web.console.session.open",
        payload=payload,
        auth_context=auth_context,
        conversation_id=conversation_id,
    )
    if not isinstance(result, dict):
        return {
            "ok": False,
            "error": {"code": "E_NODE_ERROR", "message": "invalid console open response"},
            "conversation_id": conversation_id,
        }

    if result.get("intent") == "error":
        return {
            "ok": False,
            "error": result.get("payload", {}).get("error", {"code": "E_NODE_ERROR", "message": "console open failed"}),
            "conversation_id": conversation_id,
            "route_response": result,
        }

    web_session_id = str(
        (result.get("payload", {}) if isinstance(result.get("payload", {}), dict) else {}).get("console_session_id", "")
    ).strip()
    if not web_session_id:
        return {
            "ok": False,
            "error": {"code": "E_NODE_ERROR", "message": "console session id missing"},
            "conversation_id": conversation_id,
            "route_response": result,
        }

    ensure_core_state(state)
    state.setdefault("console_sessions", {})[web_session_id] = {
        "console_session_id": web_session_id,
        "conversation_id": conversation_id,
        "actor_id": str(auth_context.get("actor_id", "")),
        "target": str(payload.get("target", "")),
        "opened_at": now_iso(),
    }
    persist_state()

    enqueue_stream_event(
        state=state,
        persist_state=persist_state,
        conversation_id=conversation_id,
        event_type="metadata",
        payload={
            "conversation_id": conversation_id,
            "console_session_id": web_session_id,
            "status": "session_ready",
        },
    )

    banner = str((result.get("payload", {}) if isinstance(result.get("payload", {}), dict) else {}).get("banner", "")).strip()
    if banner:
        enqueue_stream_event(
            state=state,
            persist_state=persist_state,
            conversation_id=conversation_id,
            event_type="delta",
            payload={"text": banner},
        )

    enqueue_stream_event(
        state=state,
        persist_state=persist_state,
        conversation_id=conversation_id,
        event_type="complete",
        payload={"intent": result.get("intent", "")},
    )

    return {
        "ok": True,
        "conversation_id": conversation_id,
        "console_session_id": web_session_id,
        "route_response": result,
    }


def handle_console_close(
    *,
    state: Dict[str, Any],
    persist_state: PersistFn,
    route_bdp_fn: Callable[..., Dict[str, Any]],
    body: Dict[str, Any],
    auth_context: Dict[str, Any],
    conversation_id: str,
) -> Dict[str, Any]:
    console_session_id = str(body.get("console_session_id", "")).strip()
    if not console_session_id:
        return {
            "ok": False,
            "error": {"code": "E_BAD_MESSAGE", "message": "console_session_id is required"},
            "conversation_id": conversation_id,
        }

    payload = {
        "console_session_id": console_session_id,
        "reason": str(body.get("reason", "requested")).strip() or "requested",
    }
    result = route_bdp_fn(
        intent="web.console.session.close",
        payload=payload,
        auth_context=auth_context,
        conversation_id=conversation_id,
    )
    if isinstance(result, dict) and result.get("intent") != "error":
        ensure_core_state(state)
        state.setdefault("console_sessions", {}).pop(console_session_id, None)
        persist_state()

    enqueue_stream_event(
        state=state,
        persist_state=persist_state,
        conversation_id=conversation_id,
        event_type="complete",
        payload={
            "intent": str(result.get("intent", "")) if isinstance(result, dict) else "",
            "console_session_id": console_session_id,
        },
    )

    return {
        "ok": isinstance(result, dict) and result.get("intent") != "error",
        "conversation_id": conversation_id,
        "console_session_id": console_session_id,
        "route_response": result if isinstance(result, dict) else {},
        "error": (result.get("payload", {}).get("error", {}) if isinstance(result, dict) and result.get("intent") == "error" else {}),
    }


def handle_console_input(
    *,
    state: Dict[str, Any],
    persist_state: PersistFn,
    route_bdp_fn: Callable[..., Dict[str, Any]],
    body: Dict[str, Any],
    auth_context: Dict[str, Any],
    conversation_id: str,
) -> Dict[str, Any]:
    console_session_id = str(body.get("console_session_id", "")).strip()
    if not console_session_id:
        return {
            "ok": False,
            "error": {"code": "E_BAD_MESSAGE", "message": "console_session_id is required"},
            "conversation_id": conversation_id,
        }

    text = str(body.get("text", "")).strip()
    event_name = str(body.get("event", "terminal.input")).strip() or "terminal.input"
    event_payload = body.get("payload", {})
    if not isinstance(event_payload, dict):
        event_payload = {}
    if text:
        event_payload = {"data": text}

    payload = {
        "console_session_id": console_session_id,
        "event": event_name,
        "payload": event_payload,
    }

    confirm = bool(body.get("confirm", False))
    approval_request_id = str(body.get("approval_request_id", "")).strip()

    result = route_bdp_fn(
        intent="web.console.session.event",
        payload=payload,
        auth_context=auth_context,
        conversation_id=conversation_id,
        confirm=confirm,
        approval_request_id=approval_request_id,
    )

    if not isinstance(result, dict):
        return {
            "ok": False,
            "error": {"code": "E_NODE_ERROR", "message": "invalid console input response"},
            "conversation_id": conversation_id,
        }

    intent = str(result.get("intent", ""))
    if intent == "web.console.session.events":
        route_payload = result.get("payload", {}) if isinstance(result.get("payload", {}), dict) else {}
        events = route_payload.get("events", [])
        if isinstance(events, list):
            for item in events:
                if not isinstance(item, dict):
                    continue
                event = str(item.get("event", "terminal.output")).strip() or "terminal.output"
                payload_obj = item.get("payload", {}) if isinstance(item.get("payload", {}), dict) else {}
                enqueue_stream_event(
                    state=state,
                    persist_state=persist_state,
                    conversation_id=conversation_id,
                    event_type=event,
                    payload=payload_obj,
                )
        enqueue_stream_event(
            state=state,
            persist_state=persist_state,
            conversation_id=conversation_id,
            event_type="complete",
            payload={"intent": intent},
        )
        return {
            "ok": True,
            "conversation_id": conversation_id,
            "console_session_id": console_session_id,
            "route_response": result,
        }

    if intent == "web.console.session.approval_required":
        route_payload = result.get("payload", {}) if isinstance(result.get("payload", {}), dict) else {}
        enqueue_stream_event(
            state=state,
            persist_state=persist_state,
            conversation_id=conversation_id,
            event_type="approval_required",
            payload=route_payload,
        )
        return {
            "ok": True,
            "conversation_id": conversation_id,
            "console_session_id": console_session_id,
            "route_response": result,
        }

    if intent == "error":
        error_obj = result.get("payload", {}).get("error", {}) if isinstance(result.get("payload", {}), dict) else {}
        enqueue_stream_event(
            state=state,
            persist_state=persist_state,
            conversation_id=conversation_id,
            event_type="error",
            payload=error_obj,
        )
        return {
            "ok": False,
            "conversation_id": conversation_id,
            "console_session_id": console_session_id,
            "route_response": result,
            "error": error_obj,
        }

    enqueue_stream_event(
        state=state,
        persist_state=persist_state,
        conversation_id=conversation_id,
        event_type="complete",
        payload={"intent": intent},
    )
    return {
        "ok": True,
        "conversation_id": conversation_id,
        "console_session_id": console_session_id,
        "route_response": result,
    }


def conversation_id_for_console_session(state: Dict[str, Any], console_session_id: str) -> str:
    ensure_core_state(state)
    if not console_session_id:
        return ""
    session = state.setdefault("console_sessions", {}).get(console_session_id)
    if not isinstance(session, dict):
        return ""
    return str(session.get("conversation_id", "")).strip()


def core_v1_conversations_open(
    *,
    state: Dict[str, Any],
    persist_state: PersistFn,
    request: Dict[str, Any],
    strict: bool = False,
) -> Dict[str, Any]:
    errors = validate_core_request_envelope(request, strict=strict)
    if errors:
        return {
            "ok": False,
            "conversation_id": str(request.get("conversation_id", "")).strip(),
            "error": {"code": "E_BAD_MESSAGE", "message": "; ".join(errors)},
        }

    auth_context = request.get("auth_context", {}) if isinstance(request.get("auth_context", {}), dict) else {}
    metadata = request.get("metadata", {}) if isinstance(request.get("metadata", {}), dict) else {}
    conversation_id = str(request.get("conversation_id", "")).strip() or f"conv_{new_uuid()}"

    convo = get_or_create_conversation(
        state=state,
        conversation_id=conversation_id,
        auth_context=auth_context,
        metadata=metadata,
    )
    persist_state()
    return {
        "ok": True,
        "conversation_id": conversation_id,
        "conversation": {
            "conversation_id": conversation_id,
            "created_at": convo.get("created_at", ""),
            "updated_at": convo.get("updated_at", ""),
            "actor_id": convo.get("actor_id", ""),
            "metadata": convo.get("metadata", {}),
        },
    }


def core_v1_messages(
    *,
    state: Dict[str, Any],
    persist_state: PersistFn,
    append_log: AppendLogFn,
    request: Dict[str, Any],
    intent_router_base_url: str,
    http_timeout_sec: float,
    library_root: str = "",
    provider_context_enabled: bool = True,
    provider_context_max_turns: int = 12,
    provider_context_max_chars: int = 12000,
    chat_sidecar_enabled: bool = True,
    post_json: HttpPostFn = http_post_json,
    strict: bool = False,
) -> Dict[str, Any]:
    errors = validate_core_request_envelope(request, strict=strict)
    if errors:
        return {
            "ok": False,
            "conversation_id": str(request.get("conversation_id", "")).strip(),
            "error": {"code": "E_BAD_MESSAGE", "message": "; ".join(errors)},
        }

    auth_context = request.get("auth_context", {}) if isinstance(request.get("auth_context", {}), dict) else {}
    conversation_id = str(request.get("conversation_id", "")).strip() or f"conv_{new_uuid()}"
    message = str(request.get("message", "")).strip()
    context = request.get("context", {}) if isinstance(request.get("context", {}), dict) else {}
    metadata = request.get("metadata", {}) if isinstance(request.get("metadata", {}), dict) else {}
    confirm = bool(request.get("confirm", False))
    approval_request_id = str(request.get("approval_request_id", "")).strip()

    body = {
        "message": message,
        "context": context,
        "metadata": metadata,
        "confirm": confirm,
        "approval_request_id": approval_request_id,
    }

    get_or_create_conversation(
        state=state,
        conversation_id=conversation_id,
        auth_context=auth_context,
        metadata=metadata,
    )
    return route_nl_message(
        state=state,
        persist_state=persist_state,
        append_log=append_log,
        intent_router_base_url=intent_router_base_url,
        http_timeout_sec=http_timeout_sec,
        body=body,
        auth_context=auth_context,
        conversation_id=conversation_id,
        library_root=library_root,
        provider_context_enabled=provider_context_enabled,
        provider_context_max_turns=provider_context_max_turns,
        provider_context_max_chars=provider_context_max_chars,
        chat_sidecar_enabled=chat_sidecar_enabled,
        post_json=post_json,
    )


def core_v1_console_open(
    *,
    state: Dict[str, Any],
    persist_state: PersistFn,
    request: Dict[str, Any],
    route_bdp_fn: Callable[..., Dict[str, Any]],
    strict: bool = False,
) -> Dict[str, Any]:
    errors = validate_core_request_envelope(request, strict=strict)
    if errors:
        return {
            "ok": False,
            "conversation_id": str(request.get("conversation_id", "")).strip(),
            "error": {"code": "E_BAD_MESSAGE", "message": "; ".join(errors)},
        }

    auth_context = request.get("auth_context", {}) if isinstance(request.get("auth_context", {}), dict) else {}
    conversation_id = str(request.get("conversation_id", "")).strip() or f"conv_{new_uuid()}"
    metadata = request.get("metadata", {}) if isinstance(request.get("metadata", {}), dict) else {}
    context = request.get("context", {}) if isinstance(request.get("context", {}), dict) else {}
    body = {
        "origin": str(context.get("origin", request.get("origin", ""))).strip(),
        "target": str(context.get("target", request.get("target", ""))).strip(),
        "source_ip": str(context.get("source_ip", request.get("source_ip", ""))).strip(),
        "metadata": metadata,
    }

    get_or_create_conversation(
        state=state,
        conversation_id=conversation_id,
        auth_context=auth_context,
        metadata=metadata,
    )
    return handle_console_open(
        state=state,
        persist_state=persist_state,
        route_bdp_fn=route_bdp_fn,
        body=body,
        auth_context=auth_context,
        conversation_id=conversation_id,
    )


def core_v1_console_close(
    *,
    state: Dict[str, Any],
    persist_state: PersistFn,
    request: Dict[str, Any],
    route_bdp_fn: Callable[..., Dict[str, Any]],
    strict: bool = False,
) -> Dict[str, Any]:
    errors = validate_core_request_envelope(request, strict=strict)
    if errors:
        return {
            "ok": False,
            "conversation_id": str(request.get("conversation_id", "")).strip(),
            "error": {"code": "E_BAD_MESSAGE", "message": "; ".join(errors)},
        }

    auth_context = request.get("auth_context", {}) if isinstance(request.get("auth_context", {}), dict) else {}
    conversation_id = str(request.get("conversation_id", "")).strip() or f"conv_{new_uuid()}"
    context = request.get("context", {}) if isinstance(request.get("context", {}), dict) else {}
    body = {
        "console_session_id": str(
            context.get(
                "console_session_id",
                request.get("console_session_id", ""),
            )
        ).strip(),
        "reason": str(context.get("reason", request.get("reason", "requested"))).strip() or "requested",
    }
    return handle_console_close(
        state=state,
        persist_state=persist_state,
        route_bdp_fn=route_bdp_fn,
        body=body,
        auth_context=auth_context,
        conversation_id=conversation_id,
    )


def core_v1_console_input(
    *,
    state: Dict[str, Any],
    persist_state: PersistFn,
    request: Dict[str, Any],
    route_bdp_fn: Callable[..., Dict[str, Any]],
    strict: bool = False,
) -> Dict[str, Any]:
    errors = validate_core_request_envelope(request, strict=strict)
    if errors:
        return {
            "ok": False,
            "conversation_id": str(request.get("conversation_id", "")).strip(),
            "error": {"code": "E_BAD_MESSAGE", "message": "; ".join(errors)},
        }

    auth_context = request.get("auth_context", {}) if isinstance(request.get("auth_context", {}), dict) else {}
    conversation_id = str(request.get("conversation_id", "")).strip() or f"conv_{new_uuid()}"
    context = request.get("context", {}) if isinstance(request.get("context", {}), dict) else {}
    body = {
        "console_session_id": str(
            context.get(
                "console_session_id",
                request.get("console_session_id", ""),
            )
        ).strip(),
        "text": str(request.get("message", context.get("text", ""))).strip(),
        "event": str(context.get("event", request.get("event", "terminal.input"))).strip() or "terminal.input",
        "payload": context.get("payload", request.get("payload", {})),
        "confirm": bool(request.get("confirm", False)),
        "approval_request_id": str(request.get("approval_request_id", "")).strip(),
    }
    return handle_console_input(
        state=state,
        persist_state=persist_state,
        route_bdp_fn=route_bdp_fn,
        body=body,
        auth_context=auth_context,
        conversation_id=conversation_id,
    )


def core_v1_stream_events(
    *,
    state: Dict[str, Any],
    persist_state: PersistFn,
    conversation_id: str,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    return pop_stream_events(
        state=state,
        persist_state=persist_state,
        conversation_id=conversation_id,
        limit=limit,
    )
