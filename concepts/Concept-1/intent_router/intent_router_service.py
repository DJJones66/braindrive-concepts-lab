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

    if any(keyword in lower for keyword in ["delete", "remove", "erase"]):
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
            "confirmation_token": "concept1-demo-token" if confirm else "",
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

    return {
        "ok": True,
        "status": "routed",
        "plan": plan,
        "route_message": outbound,
        "route_response": route_response,
    }


class IntentRouterHandler(BaseHTTPRequestHandler):
    server_version = "intent.router.natural-language/0.1"

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
            self._send_json(200, {"ok": True, "service": "intent.router.natural-language"})
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

        if self.path == "/intent/route":
            body = self._read_json()
            if body is None:
                self._send_json(400, {"ok": False, "error": "Invalid JSON"})
                return
            text = str(body.get("message", ""))
            conversation_id = body.get("conversation_id") if isinstance(body.get("conversation_id"), str) else None
            plan = _build_plan(text, conversation_id, None)
            routed = _route(plan, body, None)
            _log_local("intent_routed", {"conversation_id": conversation_id, "status": routed.get("status"), "plan": plan})
            _audit_best_effort(None, {"event": "intent_routed", "status": routed.get("status"), "plan": plan})
            self._send_json(200, routed)
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
