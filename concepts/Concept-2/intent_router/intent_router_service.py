from __future__ import annotations

import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional

from shared.bdp import (
    E_INTERNAL,
    append_jsonl,
    ensure_trace,
    http_get_json,
    http_post_json,
    make_error,
    new_uuid,
    now_iso,
    validate_core,
)

PORT = int(os.getenv("INTENT_ROUTER_PORT", "8081"))
ROUTER_BASE_URL = os.getenv("INTENT_ROUTER_ROUTER_BASE_URL", "http://node-router:8080")
SESSION_URL = os.getenv("INTENT_ROUTER_SESSION_URL", "http://node-session-state:8102/bdp")
AUDIT_URL = os.getenv("INTENT_ROUTER_AUDIT_URL", "http://node-audit-log:8103/bdp")
DATA_DIR = Path(os.getenv("INTENT_ROUTER_DATA_DIR", "/workspace/data/intent"))
LOG_FILE = DATA_DIR / "intent-events.jsonl"
UI_FILE = Path(__file__).resolve().parent / "static" / "intent_lab.html"

DEFAULT_IDENTITY = {
    "actor_id": "user.demo",
    "actor_type": "human",
    "roles": ["user"],
}

REQUIRED_EXTENSIONS = {
    "chat.general": ["identity"],
    "workflow.page.create": ["identity", "authz"],
    "workflow.interview.start": ["identity", "authz"],
    "workflow.plan.generate": ["identity"],
    "memory.delete": ["identity", "authz", "confirmation"],
    "md.library.list_notes": ["identity"],
    "md.library.read_note": ["identity"],
    "md.library.create_note": ["identity", "authz"],
    "md.library.append_note": ["identity", "authz"],
    "md.library.search_notes": ["identity"],
    "md.library.delete_note": ["identity", "authz", "confirmation"],
}


def _log_local(event_type: str, payload: Dict[str, Any]) -> None:
    append_jsonl(
        LOG_FILE,
        {
            "ts": now_iso(),
            "event_type": event_type,
            "payload": payload,
        },
    )


def _audit_best_effort(parent_message_id: Optional[str], payload: Dict[str, Any]) -> None:
    msg = {
        "protocol_version": "0.1",
        "message_id": new_uuid(),
        "intent": "audit.record",
        "payload": {
            "source": "intent.router.natural-language",
            "ts": now_iso(),
            **payload,
        },
        "extensions": {},
    }
    ensure_trace(msg, parent_message_id=parent_message_id, hop="intent.router.natural-language")
    try:
        _ = http_post_json(AUDIT_URL, msg, timeout_sec=1.0)
    except Exception:
        return


def _extract_title(text: str) -> str:
    match = re.search(r"(?:for|called|named)\s+(.+)$", text, flags=re.IGNORECASE)
    if match:
        title = match.group(1).strip()
        return title[:120] if title else "Untitled Page"
    return "Untitled Page"


def _safe_note_id(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9\-\s_]", "", value.strip().lower())
    slug = re.sub(r"[\s_]+", "-", slug).strip("-")
    return slug or "untitled-note"


def _extract_note_id_from_text(text: str) -> Optional[str]:
    patterns = [
        r"(?:append to|add to)\s+(?:note|markdown note)\s+([a-zA-Z0-9_\-\s]+?)(?:\s+(?:with|add|text|content)\b|$)",
        r"(?:read|open|show|delete|remove)\s+(?:note|markdown note)\s+([a-zA-Z0-9_\-\s]+?)(?:\s+(?:with|add|text|content|for)\b|$)",
        r"(?:note|markdown note)\s+(?:called|named)\s+([a-zA-Z0-9_\-\s]+?)(?:\s+(?:with|add|text|content|for)\b|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return _safe_note_id(match.group(1))
    return None


def _extract_note_title(text: str) -> str:
    match = re.search(
        r"(?:create|new)\s+(?:note|markdown note)\s+(?:called|named)?\s*([a-zA-Z0-9_\-\s]+?)(?:\s+(?:with|content)\b|$)",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        return match.group(1).strip()[:120] or "Untitled Note"
    return "Untitled Note"


def _extract_append_text(text: str) -> str:
    match = re.search(r"(?:append|add)\s+(?:to\s+)?(?:note|markdown note)\s+[a-zA-Z0-9_\-\s]+\s+(?:with|add|text|content)\s+(.+)$", text, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return ""


def _extract_search_query(text: str) -> str:
    match = re.search(r"(?:search\s+(?:notes|markdown notes))(?:\s+for)?\s+(.+)$", text, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return ""


def _get_context(conversation_id: Optional[str], parent_message_id: Optional[str]) -> Dict[str, Any]:
    if not conversation_id:
        return {}

    request_body = {
        "protocol_version": "0.1",
        "message_id": new_uuid(),
        "intent": "session.context.get",
        "payload": {"conversation_id": conversation_id},
        "extensions": {},
    }
    ensure_trace(request_body, parent_message_id=parent_message_id, hop="intent.router.natural-language")

    try:
        response = http_post_json(SESSION_URL, request_body, timeout_sec=2.0)
    except Exception:
        return {}

    if response.get("intent") == "error":
        return {}
    return response.get("payload", {}).get("context", {}) or {}


def _catalog() -> Dict[str, Any]:
    try:
        result = http_get_json(f"{ROUTER_BASE_URL}/router/catalog", timeout_sec=1.5)
    except Exception:
        return {}
    if not result.get("ok", False):
        return {}
    return result.get("catalog", {}) or {}


def _availability_for(capability: str) -> bool:
    catalog = _catalog()
    return capability in catalog


def _base_plan(text: str) -> Dict[str, Any]:
    return {
        "canonical_intent": "chat.general",
        "target_capability": "chat.general",
        "payload": {"text": text},
        "required_extensions": REQUIRED_EXTENSIONS["chat.general"],
        "confidence": 0.88,
        "risk_class": "read",
        "clarification_required": False,
        "confirmation_required": False,
        "user_prompt": "",
        "reason_codes": ["fallback_chat_general"],
    }


def _build_plan(text: str, conversation_id: Optional[str], parent_message_id: Optional[str]) -> Dict[str, Any]:
    cleaned = text.strip()
    lower = cleaned.lower()
    context = _get_context(conversation_id, parent_message_id)

    plan = _base_plan(cleaned)

    if not cleaned:
        plan["confidence"] = 0.5
        plan["clarification_required"] = True
        plan["user_prompt"] = "I need some text to determine your intent."
        plan["reason_codes"] = ["empty_message"]
        return plan

    if any(keyword in lower for keyword in ["list notes", "show notes", "list markdown notes"]):
        plan.update(
            {
                "canonical_intent": "md.library.list_notes",
                "target_capability": "md.library.list_notes",
                "payload": {},
                "required_extensions": REQUIRED_EXTENSIONS["md.library.list_notes"],
                "confidence": 0.95,
                "risk_class": "read",
                "reason_codes": ["keyword_list_notes"],
            }
        )

    elif any(keyword in lower for keyword in ["read note", "open note", "show note"]):
        note_id = _extract_note_id_from_text(cleaned)
        plan.update(
            {
                "canonical_intent": "md.library.read_note",
                "target_capability": "md.library.read_note",
                "payload": {"note_id": note_id or ""},
                "required_extensions": REQUIRED_EXTENSIONS["md.library.read_note"],
                "confidence": 0.91,
                "risk_class": "read",
                "reason_codes": ["keyword_read_note"],
            }
        )
        if not note_id:
            plan["clarification_required"] = True
            plan["user_prompt"] = "Which note should I read? Include the note name."
            plan["reason_codes"].append("missing_note_id")

    elif any(keyword in lower for keyword in ["create note", "new note", "create markdown note"]):
        title = _extract_note_title(cleaned)
        content = ""
        content_match = re.search(r"(?:with|content)\s+(.+)$", cleaned, flags=re.IGNORECASE)
        if content_match:
            content = content_match.group(1).strip()
        plan.update(
            {
                "canonical_intent": "md.library.create_note",
                "target_capability": "md.library.create_note",
                "payload": {
                    "title": title,
                    "content": content,
                },
                "required_extensions": REQUIRED_EXTENSIONS["md.library.create_note"],
                "confidence": 0.94,
                "risk_class": "mutate",
                "confirmation_required": True,
                "reason_codes": ["keyword_create_note"],
            }
        )

    elif any(keyword in lower for keyword in ["append to note", "add to note", "append note"]):
        note_id = _extract_note_id_from_text(cleaned)
        append_text = _extract_append_text(cleaned)
        plan.update(
            {
                "canonical_intent": "md.library.append_note",
                "target_capability": "md.library.append_note",
                "payload": {
                    "note_id": note_id or "",
                    "append_text": append_text,
                },
                "required_extensions": REQUIRED_EXTENSIONS["md.library.append_note"],
                "confidence": 0.9,
                "risk_class": "mutate",
                "confirmation_required": True,
                "reason_codes": ["keyword_append_note"],
            }
        )
        if not note_id or not append_text:
            plan["clarification_required"] = True
            plan["user_prompt"] = "Provide the target note and text to append."
            plan["reason_codes"].append("append_payload_incomplete")

    elif any(keyword in lower for keyword in ["search notes", "find note", "search markdown notes"]):
        query = _extract_search_query(cleaned)
        plan.update(
            {
                "canonical_intent": "md.library.search_notes",
                "target_capability": "md.library.search_notes",
                "payload": {
                    "query": query,
                },
                "required_extensions": REQUIRED_EXTENSIONS["md.library.search_notes"],
                "confidence": 0.9,
                "risk_class": "read",
                "reason_codes": ["keyword_search_notes"],
            }
        )
        if not query:
            plan["clarification_required"] = True
            plan["user_prompt"] = "What should I search for in notes?"
            plan["reason_codes"].append("missing_search_query")

    elif any(keyword in lower for keyword in ["delete note", "delete markdown note", "remove note"]):
        note_id = _extract_note_id_from_text(cleaned)
        plan.update(
            {
                "canonical_intent": "md.library.delete_note",
                "target_capability": "md.library.delete_note",
                "payload": {
                    "note_id": note_id or "",
                },
                "required_extensions": REQUIRED_EXTENSIONS["md.library.delete_note"],
                "confidence": 0.91,
                "risk_class": "destructive",
                "confirmation_required": True,
                "reason_codes": ["keyword_delete_note"],
            }
        )
        if not note_id:
            plan["clarification_required"] = True
            plan["user_prompt"] = "Which note should I delete?"
            plan["reason_codes"].append("missing_note_id")

    elif any(keyword in lower for keyword in ["delete", "remove", "erase"]):
        target = context.get("last_draft_id")
        plan.update(
            {
                "canonical_intent": "memory.delete",
                "target_capability": "memory.delete",
                "payload": {"target_id": target, "text": cleaned},
                "required_extensions": REQUIRED_EXTENSIONS["memory.delete"],
                "confidence": 0.9,
                "risk_class": "destructive",
                "confirmation_required": True,
                "reason_codes": ["keyword_delete"],
            }
        )
        if not target and any(token in lower for token in ["that", "this", "old", "same"]):
            plan["clarification_required"] = True
            plan["user_prompt"] = "Which draft should I delete? I could not resolve the reference from session context."
            plan["reason_codes"].append("missing_referent")

    elif any(keyword in lower for keyword in ["new page", "create page", "start a page"]):
        plan.update(
            {
                "canonical_intent": "workflow.page.create",
                "target_capability": "workflow.page.create",
                "payload": {
                    "title": _extract_title(cleaned),
                    "source_text": cleaned,
                },
                "required_extensions": REQUIRED_EXTENSIONS["workflow.page.create"],
                "confidence": 0.91,
                "risk_class": "mutate",
                "confirmation_required": True,
                "reason_codes": ["keyword_page_create"],
            }
        )

    elif any(keyword in lower for keyword in ["interview me", "start interview"]):
        plan.update(
            {
                "canonical_intent": "workflow.interview.start",
                "target_capability": "workflow.interview.start",
                "payload": {
                    "topic": context.get("last_topic") or "general",
                    "source_text": cleaned,
                },
                "required_extensions": REQUIRED_EXTENSIONS["workflow.interview.start"],
                "confidence": 0.9,
                "risk_class": "mutate",
                "confirmation_required": True,
                "reason_codes": ["keyword_interview_start"],
            }
        )

    elif all(keyword in lower for keyword in ["generate", "plan"]):
        reference = context.get("last_spec_id") if "same as before" in lower else None
        plan.update(
            {
                "canonical_intent": "workflow.plan.generate",
                "target_capability": "workflow.plan.generate",
                "payload": {
                    "reference_spec_id": reference,
                    "source_text": cleaned,
                },
                "required_extensions": REQUIRED_EXTENSIONS["workflow.plan.generate"],
                "confidence": 0.87,
                "risk_class": "read",
                "reason_codes": ["keyword_plan_generate"],
            }
        )
        if "same as before" in lower and not reference:
            plan["clarification_required"] = True
            plan["user_prompt"] = "I could not resolve 'same as before'. Which spec should I use?"
            plan["reason_codes"].append("missing_reference_spec")

    if not _availability_for(plan["target_capability"]):
        fallback_available = _availability_for("chat.general")
        if fallback_available:
            original_target = plan["target_capability"]
            plan = _base_plan(cleaned)
            plan["reason_codes"].extend(["capability_unavailable", f"fallback_from:{original_target}"])
            plan["user_prompt"] = "I could not route the requested action directly. I routed to general chat instead."
        else:
            plan["clarification_required"] = True
            plan["user_prompt"] = "Requested capability is unavailable and no safe fallback route exists."
            plan["reason_codes"].append("no_route_available")

    if plan["risk_class"] == "read":
        if 0.65 <= float(plan["confidence"]) < 0.85:
            plan["clarification_required"] = True
            if not plan["user_prompt"]:
                plan["user_prompt"] = "I need clarification before I can route this request safely."
            plan["reason_codes"].append("confidence_mid")
        elif float(plan["confidence"]) < 0.65:
            plan["clarification_required"] = True
            plan["user_prompt"] = "I could not confidently map this request. Please clarify your goal."
            plan["reason_codes"].append("confidence_low")

    return plan


def _analyze(text: str, conversation_id: Optional[str], parent_message_id: Optional[str]) -> Dict[str, Any]:
    normalized = text.strip()
    plan = _build_plan(text, conversation_id, parent_message_id)
    return {
        "message": text,
        "normalized_message": normalized,
        "conversation_id": conversation_id,
        "canonical_intent": plan.get("canonical_intent"),
        "confidence": plan.get("confidence"),
        "risk_class": plan.get("risk_class"),
        "clarification_required": plan.get("clarification_required"),
        "confirmation_required": plan.get("confirmation_required"),
        "reason_codes": plan.get("reason_codes", []),
        "required_extensions": plan.get("required_extensions", []),
        "plan": plan,
    }


def _build_route_message(
    plan: Dict[str, Any],
    identity: Dict[str, Any],
    authz: Optional[Dict[str, Any]],
    conversation_id: Optional[str],
    confirm: bool,
    parent_message_id: Optional[str],
) -> Dict[str, Any]:
    extensions: Dict[str, Any] = {
        "identity": identity,
        "intent_plan": {
            "canonical_intent": plan["canonical_intent"],
            "confidence": plan["confidence"],
            "risk_class": plan["risk_class"],
            "reason_codes": plan.get("reason_codes", []),
        },
    }

    if authz:
        extensions["authz"] = authz

    if conversation_id:
        extensions["session"] = {"conversation_id": conversation_id}

    if plan.get("confirmation_required") or confirm:
        extensions["confirmation"] = {
            "confirmed": bool(confirm),
            "confirmation_token": "concept2-demo-token" if confirm else "",
        }

    message = {
        "protocol_version": "0.1",
        "message_id": new_uuid(),
        "intent": plan["canonical_intent"],
        "payload": plan["payload"],
        "extensions": extensions,
    }
    ensure_trace(message, parent_message_id=parent_message_id, hop="intent.router.natural-language")
    return message


def _route(plan: Dict[str, Any], request_obj: Dict[str, Any], parent_message_id: Optional[str]) -> Dict[str, Any]:
    identity = request_obj.get("identity") if isinstance(request_obj.get("identity"), dict) else DEFAULT_IDENTITY
    authz = request_obj.get("authz") if isinstance(request_obj.get("authz"), dict) else None
    conversation_id = request_obj.get("conversation_id") if isinstance(request_obj.get("conversation_id"), str) else None
    confirm = bool(request_obj.get("confirm", False))

    if plan.get("clarification_required"):
        return {
            "ok": True,
            "status": "needs_clarification",
            "plan": plan,
        }

    if plan.get("confirmation_required") and not confirm:
        prompt = plan.get("user_prompt") or "This action requires confirmation. Re-send with confirm=true."
        return {
            "ok": True,
            "status": "needs_confirmation",
            "plan": {**plan, "user_prompt": prompt},
        }

    outbound = _build_route_message(plan, identity, authz, conversation_id, confirm, parent_message_id)

    try:
        route_response = http_post_json(f"{ROUTER_BASE_URL}/route", outbound, timeout_sec=3.0)
    except Exception as exc:
        return {
            "ok": False,
            "status": "router_unavailable",
            "plan": plan,
            "error": str(exc),
        }

    status = "routed"
    if route_response.get("intent") == "error":
        status = "route_error"

    return {
        "ok": True,
        "status": status,
        "plan": plan,
        "route_message": outbound,
        "route_response": route_response,
    }


def _capability_snapshot() -> Dict[str, Any]:
    catalog = _catalog()
    fake_caps: List[Dict[str, Any]] = []

    for capability, entries in sorted(catalog.items()):
        if not capability.startswith("md.library."):
            continue

        nodes: List[str] = []
        required: List[str] = []
        if isinstance(entries, list):
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                node_id = entry.get("node_id")
                if isinstance(node_id, str):
                    nodes.append(node_id)
                req = entry.get("required_extensions", [])
                if isinstance(req, list):
                    required.extend([v for v in req if isinstance(v, str)])

        fake_caps.append(
            {
                "capability": capability,
                "nodes": sorted(set(nodes)),
                "required_extensions": sorted(set(required)),
            }
        )

    return {
        "ok": True,
        "fake_capabilities": fake_caps,
        "catalog": catalog,
    }


class IntentRouterHandler(BaseHTTPRequestHandler):
    server_version = "intent.router.natural-language/0.2"

    def _send_json(self, code: int, body: Dict[str, Any]) -> None:
        payload = json.dumps(body, ensure_ascii=True).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_html(self, code: int, body: str) -> None:
        payload = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
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
            self._send_json(200, {"ok": True, "service": "intent.router.natural-language", "ui": "/ui"})
            return

        if self.path in {"/", "/ui"}:
            if not UI_FILE.exists():
                self._send_html(404, "<h1>Intent Lab UI file not found</h1>")
                return
            self._send_html(200, UI_FILE.read_text(encoding="utf-8"))
            return

        if self.path == "/intent/capabilities":
            self._send_json(200, _capability_snapshot())
            return

        self._send_json(404, {"ok": False})

    def do_POST(self) -> None:
        if self.path == "/intent/plan":
            body = self._read_json()
            if body is None:
                self._send_json(400, {"ok": False, "error": "Invalid JSON"})
                return
            text = str(body.get("message", ""))
            conversation_id = body.get("conversation_id") if isinstance(body.get("conversation_id"), str) else None
            plan = _build_plan(text, conversation_id, None)
            _log_local("intent_plan_built", {"conversation_id": conversation_id, "plan": plan})
            _audit_best_effort(None, {"event": "intent_plan_built", "plan": plan})
            self._send_json(200, {"ok": True, "plan": plan})
            return

        if self.path == "/intent/analyze":
            body = self._read_json()
            if body is None:
                self._send_json(400, {"ok": False, "error": "Invalid JSON"})
                return

            text = str(body.get("message", ""))
            conversation_id = body.get("conversation_id") if isinstance(body.get("conversation_id"), str) else None
            analysis = _analyze(text, conversation_id, None)
            _log_local("intent_analyzed", {"conversation_id": conversation_id, "analysis": analysis})
            _audit_best_effort(None, {"event": "intent_analyzed", "analysis": analysis})
            self._send_json(
                200,
                {
                    "ok": True,
                    "status": "analyzed",
                    "analysis": analysis,
                    "plan": analysis.get("plan"),
                },
            )
            return

        if self.path == "/intent/route":
            body = self._read_json()
            if body is None:
                self._send_json(400, {"ok": False, "error": "Invalid JSON"})
                return
            text = str(body.get("message", ""))
            conversation_id = body.get("conversation_id") if isinstance(body.get("conversation_id"), str) else None
            analysis = _analyze(text, conversation_id, None)
            plan = analysis.get("plan", {})
            routed = _route(plan, body, None)
            _log_local("intent_routed", {"conversation_id": conversation_id, "status": routed.get("status"), "analysis": analysis})
            _audit_best_effort(None, {"event": "intent_routed", "status": routed.get("status"), "analysis": analysis})
            self._send_json(200, {**routed, "analysis": analysis})
            return

        if self.path == "/bdp":
            message = self._read_json()
            if message is None:
                self._send_json(200, make_error("E_BAD_MESSAGE", "Invalid JSON body", None))
                return

            validation_error = validate_core(message)
            if validation_error:
                self._send_json(200, validation_error)
                return

            if message.get("intent") not in {
                "intent.router.build_plan",
                "intent.router.parse_message",
            }:
                self._send_json(
                    200,
                    make_error(
                        "E_NO_ROUTE",
                        f"intent.router.natural-language cannot handle intent {message.get('intent')}",
                        message.get("message_id"),
                    ),
                )
                return

            try:
                text = str(message.get("payload", {}).get("message", ""))
                conversation_id = message.get("payload", {}).get("conversation_id")
                if conversation_id is not None and not isinstance(conversation_id, str):
                    conversation_id = None
                plan = _build_plan(text, conversation_id, message.get("message_id"))
                response = {
                    "protocol_version": "0.1",
                    "message_id": new_uuid(),
                    "intent": "intent_plan",
                    "payload": {"intent_plan": plan},
                    "extensions": {},
                }
                ensure_trace(response, parent_message_id=message.get("message_id"), hop="intent.router.natural-language")
                self._send_json(200, response)
            except Exception as exc:
                self._send_json(
                    200,
                    make_error(
                        E_INTERNAL,
                        f"intent router exception: {type(exc).__name__}",
                        message.get("message_id"),
                        details={"error": str(exc)},
                    ),
                )
            return

        self._send_json(404, {"ok": False})

    def log_message(self, format: str, *args: Any) -> None:
        return


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("0.0.0.0", PORT), IntentRouterHandler)
    print(f"intent.router.natural-language listening on :{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
