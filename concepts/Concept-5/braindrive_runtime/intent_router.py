from __future__ import annotations

import re
import time
from typing import Any, Dict, Optional

from .constants import E_NO_ROUTE
from .protocol import make_error, new_uuid
from .router import RouterCore


class IntentRouterNL:
    def __init__(self, router: RouterCore, confidence_threshold: float = 0.75, catalog_ttl_sec: float = 5.0) -> None:
        self.router = router
        self.confidence_threshold = confidence_threshold
        self.catalog_ttl_sec = catalog_ttl_sec
        self._catalog_cached: Dict[str, Any] = {}
        self._catalog_loaded_at = 0.0

    def _catalog(self) -> Dict[str, Any]:
        now = time.time()
        if now - self._catalog_loaded_at > self.catalog_ttl_sec:
            self._catalog_cached = self.router.catalog()
            self._catalog_loaded_at = now
        return self._catalog_cached

    def _has_capability(self, capability: str) -> bool:
        return capability in self._catalog()

    def _metadata_for(self, capability: str) -> Dict[str, Any]:
        entries = self._catalog().get(capability, [])
        if not isinstance(entries, list) or not entries:
            return {}
        if not isinstance(entries[0], dict):
            return {}
        return entries[0]

    def _infer_topic(self, text: str) -> str:
        match = re.search(r"(?:for|about)\s+(.+)$", text, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()
        return text.strip() or "untitled"

    @staticmethod
    def _clean_label(text: str) -> str:
        value = text.strip()
        if len(value) >= 2 and (
            (value.startswith('"') and value.endswith('"'))
            or (value.startswith("'") and value.endswith("'"))
        ):
            value = value[1:-1].strip()
        value = re.sub(r"[.?!]+$", "", value).strip()
        return value or "untitled"

    def _extract_folder_topic(self, text: str) -> str:
        match = re.search(
            r"(?:create|new|start)\s+(?:a\s+)?folder(?:\s+(?:called|named|for|about))?\s+(.+)$",
            text,
            flags=re.IGNORECASE,
        )
        if match:
            return self._clean_label(match.group(1))
        return self._clean_label(self._infer_topic(text))

    @staticmethod
    def _active_folder_from_context(context: Optional[Dict[str, Any]]) -> str:
        if not isinstance(context, dict):
            return ""
        value = context.get("active_folder", "")
        if not isinstance(value, str):
            return ""
        return value.strip()

    def _resolve_active_folder(self, context: Optional[Dict[str, Any]]) -> str:
        from_context = self._active_folder_from_context(context)
        if from_context:
            return from_context

        if not self._has_capability("folder.list"):
            return ""

        probe_message = {
            "protocol_version": "0.1",
            "message_id": new_uuid(),
            "intent": "folder.list",
            "payload": {},
        }
        try:
            probe = self.router.route_for_test(probe_message)
        except Exception:
            return ""
        if not isinstance(probe, dict):
            return ""
        payload = probe.get("payload", {})
        if not isinstance(payload, dict):
            return ""
        active = payload.get("active_folder", "")
        if not isinstance(active, str):
            return ""
        return active.strip()

    @staticmethod
    def _context_awaiting_interview_answer(context: Optional[Dict[str, Any]]) -> bool:
        if not isinstance(context, dict):
            return False
        interview = context.get("interview", {})
        if isinstance(interview, dict) and bool(interview.get("awaiting_answer", False)):
            return True
        return bool(context.get("awaiting_interview_answer", False))

    @staticmethod
    def _extract_urls(text: str) -> list[str]:
        raw = re.findall(r"https?://[^\s,]+", text, flags=re.IGNORECASE)
        cleaned: list[str] = []
        for item in raw:
            value = item.strip().rstrip(").,;!?]")
            if value:
                cleaned.append(value)
        return cleaned

    def _scrape_intent_plan(self, cleaned: str, lower: str) -> Optional[Dict[str, Any]]:
        scrape_keywords = ["scrape", "extract from", "get webpage content", "webpage content"]
        dynamic_keywords = ["dynamic", "render js", "browser fetch", "javascript-rendered", "js-rendered"]
        stealth_keywords = ["stealth", "cloudflare", "turnstile", "anti-bot"]

        has_scrape_keyword = any(token in lower for token in scrape_keywords)
        has_dynamic_keyword = any(token in lower for token in dynamic_keywords)
        has_stealth_keyword = any(token in lower for token in stealth_keywords)
        if not any([has_scrape_keyword, has_dynamic_keyword, has_stealth_keyword]):
            return None

        urls = self._extract_urls(cleaned)
        if not urls:
            return {
                "canonical_intent": "web.scrape.get",
                "confidence": 0.85,
                "risk_class": "read",
                "reason_codes": ["keyword_scrape_missing_url"],
                "required_extensions": [],
                "payload": {},
                "clarification_required": True,
                "required_confirmation": False,
                "clarification_prompt": "Please include at least one URL to scrape.",
            }

        extraction_type = "markdown"
        if " html" in lower or "as html" in lower:
            extraction_type = "html"
        elif " plain text" in lower or " as text" in lower or " text only" in lower:
            extraction_type = "text"

        is_bulk = len(urls) > 1 or any(token in lower for token in ["bulk", "multiple urls", "all urls", "batch"])
        mode = "get"
        reason_codes = ["keyword_scrape_get"]
        confidence = 0.9
        required_confirmation = False
        if has_stealth_keyword:
            mode = "stealth_fetch"
            reason_codes = ["keyword_scrape_stealth"]
            confidence = 0.92
            required_confirmation = True
        elif has_dynamic_keyword:
            mode = "fetch"
            reason_codes = ["keyword_scrape_dynamic"]
            confidence = 0.91

        intent = f"web.scrape.bulk_{mode}" if is_bulk else f"web.scrape.{mode}"
        payload: Dict[str, Any] = {"extraction_type": extraction_type}
        if is_bulk:
            payload["urls"] = urls
        else:
            payload["url"] = urls[0]

        return {
            "canonical_intent": intent,
            "confidence": confidence,
            "risk_class": "read",
            "reason_codes": reason_codes,
            "required_extensions": [],
            "payload": payload,
            "clarification_required": False,
            "required_confirmation": required_confirmation,
        }

    @staticmethod
    def _extract_web_console_target(cleaned: str) -> str:
        patterns = [
            r"(?:target|for)\s+([a-zA-Z0-9._-]+)$",
            r"/use\s+([a-zA-Z0-9._-]+)$",
        ]
        for pattern in patterns:
            match = re.search(pattern, cleaned, flags=re.IGNORECASE)
            if match:
                return match.group(1).strip()
        return ""

    def _analyze_intent(self, text: str, context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        cleaned = text.strip()
        lower = cleaned.lower()
        plan: Dict[str, Any] = {
            "canonical_intent": "model.chat.complete",
            "confidence": 0.86,
            "risk_class": "read",
            "reason_codes": ["fallback_model_chat"],
            "required_extensions": [],
            "payload": {"prompt": cleaned},
            "clarification_required": False,
            "required_confirmation": False,
        }

        if not cleaned:
            plan["confidence"] = 0.4
            plan["clarification_required"] = True
            plan["reason_codes"] = ["empty_prompt"]
            plan["clarification_prompt"] = "Please share what you want to do."
            return plan

        scrape_plan = self._scrape_intent_plan(cleaned, lower)
        if scrape_plan is not None:
            plan.update(scrape_plan)

        elif self._context_awaiting_interview_answer(context) and not any(
            token in lower for token in ["complete interview", "finish interview"]
        ):
            answer = cleaned.split(":", 1)[1].strip() if ":" in cleaned else cleaned
            plan.update(
                {
                    "canonical_intent": "workflow.interview.continue",
                    "confidence": 0.89,
                    "reason_codes": ["context_interview_awaiting_answer"],
                    "payload": {"answer": answer},
                }
            )

        elif any(token in lower for token in ["web terminal", "web console", "browser terminal", "terminal in browser"]):
            if re.search(r"\b(list|show|available)\b.*\btargets?\b", lower):
                plan.update(
                    {
                        "canonical_intent": "web.console.targets.list",
                        "confidence": 0.9,
                        "reason_codes": ["keyword_web_console_targets"],
                        "payload": {},
                    }
                )
            elif re.search(r"\b(list|show)\b.*\bguides?\b", lower) or "terminal guides" in lower:
                plan.update(
                    {
                        "canonical_intent": "web.console.guides.list",
                        "confidence": 0.88,
                        "reason_codes": ["keyword_web_console_guides"],
                        "payload": {},
                    }
                )
            elif "close" in lower:
                session_match = re.search(r"(sess_[a-z0-9-]+)", cleaned, flags=re.IGNORECASE)
                if session_match is None:
                    plan.update(
                        {
                            "canonical_intent": "web.console.session.close",
                            "confidence": 0.85,
                            "reason_codes": ["keyword_web_console_close_missing_session"],
                            "payload": {},
                            "clarification_required": True,
                            "clarification_prompt": "Please include the web terminal session_id (for example sess_...).",
                        }
                    )
                else:
                    plan.update(
                        {
                            "canonical_intent": "web.console.session.close",
                            "confidence": 0.9,
                            "reason_codes": ["keyword_web_console_close"],
                            "payload": {"session_id": session_match.group(1)},
                        }
                    )
            else:
                origin = ""
                if isinstance(context, dict):
                    origin_value = context.get("origin", "")
                    origin = str(origin_value).strip() if isinstance(origin_value, str) else ""
                if not origin:
                    plan.update(
                        {
                            "canonical_intent": "web.console.session.open",
                            "confidence": 0.86,
                            "reason_codes": ["keyword_web_console_open_missing_origin"],
                            "payload": {},
                            "clarification_required": True,
                            "clarification_prompt": "Please include the browser origin to open a web terminal session.",
                        }
                    )
                else:
                    payload: Dict[str, Any] = {"origin": origin}
                    target = self._extract_web_console_target(cleaned)
                    if target:
                        payload["target"] = target
                    plan.update(
                        {
                            "canonical_intent": "web.console.session.open",
                            "confidence": 0.9,
                            "reason_codes": ["keyword_web_console_open"],
                            "payload": payload,
                        }
                    )

        elif re.search(r"\blist(?:\s+\w+){0,3}\s+folders?\b", lower) or lower in {"folders", "list folder"}:
            plan.update(
                {
                    "canonical_intent": "folder.list",
                    "confidence": 0.96,
                    "reason_codes": ["keyword_folder_list"],
                    "payload": {},
                }
            )

        elif any(token in lower for token in ["create folder", "new folder", "start folder"]):
            topic = self._extract_folder_topic(cleaned)
            plan.update(
                {
                    "canonical_intent": "folder.create",
                    "confidence": 0.95,
                    "risk_class": "mutate",
                    "required_confirmation": True,
                    "reason_codes": ["keyword_folder_create"],
                    "payload": {"topic": topic},
                }
            )

        elif any(token in lower for token in ["switch folder", "work on folder", "go to folder"]):
            folder_match = re.search(
                r"(?:switch(?:\s+folder)?\s+(?:to\s+)?)|(?:work\s+on\s+folder\s+)|(?:go\s+to\s+folder\s+)",
                lower,
            )
            folder = self._infer_topic(cleaned)
            if folder_match:
                start = folder_match.end()
                candidate = cleaned[start:].strip()
                if candidate:
                    folder = candidate
            plan.update(
                {
                    "canonical_intent": "folder.switch",
                    "confidence": 0.91,
                    "reason_codes": ["keyword_folder_switch"],
                    "payload": {"folder": folder.replace(" ", "-").lower()},
                }
            )

        elif any(token in lower for token in ["start interview", "interview me"]):
            plan.update(
                {
                    "canonical_intent": "workflow.interview.start",
                    "confidence": 0.92,
                    "reason_codes": ["keyword_interview_start"],
                    "payload": {},
                }
            )

        elif any(token in lower for token in ["continue interview", "my answer", "answer:"]):
            answer = cleaned.split(":", 1)[1].strip() if ":" in cleaned else cleaned
            plan.update(
                {
                    "canonical_intent": "workflow.interview.continue",
                    "confidence": 0.85,
                    "reason_codes": ["keyword_interview_continue"],
                    "payload": {"answer": answer},
                }
            )

        elif any(token in lower for token in ["complete interview", "finish interview"]):
            plan.update(
                {
                    "canonical_intent": "workflow.interview.complete",
                    "confidence": 0.9,
                    "reason_codes": ["keyword_interview_complete"],
                    "payload": {},
                }
            )

        elif any(token in lower for token in ["generate spec", "draft spec"]):
            plan.update(
                {
                    "canonical_intent": "workflow.spec.generate",
                    "confidence": 0.9,
                    "reason_codes": ["keyword_spec_generate"],
                    "payload": {},
                }
            )

        elif any(token in lower for token in ["save spec", "propose spec"]):
            plan.update(
                {
                    "canonical_intent": "workflow.spec.propose_save",
                    "confidence": 0.9,
                    "risk_class": "mutate",
                    "required_confirmation": False,
                    "reason_codes": ["keyword_spec_propose_save"],
                    "payload": {},
                }
            )

        elif any(token in lower for token in ["generate plan", "draft plan"]):
            plan.update(
                {
                    "canonical_intent": "workflow.plan.generate",
                    "confidence": 0.89,
                    "reason_codes": ["keyword_plan_generate"],
                    "payload": {},
                }
            )

        elif any(token in lower for token in ["save plan", "propose plan"]):
            plan.update(
                {
                    "canonical_intent": "workflow.plan.propose_save",
                    "confidence": 0.89,
                    "risk_class": "mutate",
                    "reason_codes": ["keyword_plan_propose_save"],
                    "payload": {},
                }
            )

        elif any(token in lower for token in ["read file", "open file"]):
            path = self._infer_topic(cleaned)
            plan.update(
                {
                    "canonical_intent": "memory.read",
                    "confidence": 0.84,
                    "reason_codes": ["keyword_memory_read"],
                    "payload": {"path": path},
                }
            )

        elif "list files" in lower:
            active_folder = self._resolve_active_folder(context)
            list_path = active_folder or "."
            reason_codes = ["keyword_memory_list"]
            reason_codes.append("active_folder_scope" if active_folder else "library_root_scope")
            plan.update(
                {
                    "canonical_intent": "memory.list",
                    "confidence": 0.9,
                    "reason_codes": reason_codes,
                    "payload": {"path": list_path},
                }
            )

        elif any(token in lower for token in ["search files", "search notes"]):
            query = self._infer_topic(cleaned)
            plan.update(
                {
                    "canonical_intent": "memory.search",
                    "confidence": 0.9,
                    "reason_codes": ["keyword_memory_search"],
                    "payload": {"query": query},
                }
            )

        elif any(token in lower for token in ["write file", "save file"]):
            plan.update(
                {
                    "canonical_intent": "memory.write.propose",
                    "confidence": 0.88,
                    "risk_class": "mutate",
                    "required_confirmation": True,
                    "reason_codes": ["keyword_memory_write"],
                    "payload": {"path": "notes.md", "content": cleaned},
                }
            )

        elif any(token in lower for token in ["edit file", "update file"]):
            plan.update(
                {
                    "canonical_intent": "memory.edit.propose",
                    "confidence": 0.83,
                    "risk_class": "mutate",
                    "required_confirmation": True,
                    "reason_codes": ["keyword_memory_edit"],
                    "payload": {"path": "notes.md", "content": cleaned},
                }
            )

        elif any(token in lower for token in ["delete file", "remove file"]):
            plan.update(
                {
                    "canonical_intent": "memory.delete.propose",
                    "confidence": 0.86,
                    "risk_class": "destructive",
                    "required_confirmation": True,
                    "reason_codes": ["keyword_memory_delete"],
                    "payload": {"path": "notes.md"},
                }
            )

        elif any(token in lower for token in ["list models", "model catalog"]):
            plan.update(
                {
                    "canonical_intent": "model.catalog.list",
                    "confidence": 0.93,
                    "reason_codes": ["keyword_model_catalog"],
                    "payload": {},
                }
            )

        elif any(token in lower for token in ["ask model", "complete with model"]):
            prompt = cleaned.split("model", 1)[-1].strip() if "model" in cleaned else cleaned
            plan.update(
                {
                    "canonical_intent": "model.chat.complete",
                    "confidence": 0.85,
                    "reason_codes": ["keyword_model_complete"],
                    "payload": {"prompt": prompt or cleaned},
                }
            )

        elif any(token in lower for token in ["stream model", "stream response"]):
            plan.update(
                {
                    "canonical_intent": "model.chat.stream",
                    "confidence": 0.85,
                    "reason_codes": ["keyword_model_stream"],
                    "payload": {"prompt": cleaned},
                }
            )

        metadata = self._metadata_for(plan["canonical_intent"])
        if metadata:
            plan["risk_class"] = metadata.get("risk_class", plan["risk_class"])
            plan["required_extensions"] = metadata.get("required_extensions", [])
            plan["required_confirmation"] = bool(metadata.get("approval_required", plan["required_confirmation"]))
        else:
            plan["required_extensions"] = []

        if not self._has_capability(plan["canonical_intent"]):
            plan["clarification_required"] = True
            plan["error_code"] = E_NO_ROUTE
            plan["reason_codes"].append("capability_unavailable")
            plan["clarification_prompt"] = "That capability is currently unavailable."

        if float(plan["confidence"]) < self.confidence_threshold:
            plan["clarification_required"] = True
            plan["reason_codes"].append("confidence_below_threshold")
            plan.setdefault("clarification_prompt", "I need clarification before routing this request.")

        return plan

    def analyze(self, text: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        plan = self._analyze_intent(text, context)
        return {
            "canonical_intent": plan["canonical_intent"],
            "confidence": plan["confidence"],
            "risk_class": plan["risk_class"],
            "reason_codes": plan["reason_codes"],
            "required_extensions": plan["required_extensions"],
            "target_capabilities": [plan["canonical_intent"]],
            "clarification_required": plan["clarification_required"],
            "clarification_prompt": plan.get("clarification_prompt", ""),
            "payload": plan["payload"],
            "required_confirmation": plan.get("required_confirmation", False),
            "error_code": plan.get("error_code"),
        }

    def route(
        self,
        text: str,
        *,
        context: Optional[Dict[str, Any]] = None,
        confirm: bool = False,
        request_extensions: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        analysis = self.analyze(text, context=context)
        if analysis["clarification_required"]:
            return {
                "ok": True,
                "status": "needs_clarification",
                "analysis": analysis,
            }

        extensions: Dict[str, Any] = {
            "confidence": {
                "score": analysis["confidence"],
                "basis": "intent.router.nl",
            }
        }

        if request_extensions:
            for key, value in request_extensions.items():
                extensions[key] = value

        if analysis["required_confirmation"]:
            extensions["confirmation"] = {
                "required": True,
                "status": "approved" if confirm else "pending",
                "request_id": str(new_uuid()),
            }

        message = {
            "protocol_version": "0.1",
            "message_id": new_uuid(),
            "intent": analysis["canonical_intent"],
            "payload": analysis["payload"],
            "extensions": extensions,
        }

        route_response = self.router.route(message)
        status = "routed"
        if route_response.get("intent") == "error":
            status = "route_error"

        return {
            "ok": True,
            "status": status,
            "analysis": analysis,
            "route_message": message,
            "route_response": route_response,
        }

    def test_route(self, message: Dict[str, Any]) -> Dict[str, Any]:
        return self.router.route_for_test(message)

    def capabilities(self) -> Dict[str, Any]:
        return {
            "ok": True,
            "catalog": self.router.catalog(),
        }

    def analyze_endpoint(self, message: Dict[str, Any]) -> Dict[str, Any]:
        text = str(message.get("message", ""))
        context = message.get("context", {}) if isinstance(message.get("context"), dict) else None
        return {"ok": True, "analysis": self.analyze(text, context=context)}

    def route_endpoint(self, message: Dict[str, Any]) -> Dict[str, Any]:
        text = str(message.get("message", ""))
        confirm = bool(message.get("confirm", False))
        context = message.get("context", {}) if isinstance(message.get("context"), dict) else None
        request_extensions = message.get("extensions", {}) if isinstance(message.get("extensions"), dict) else {}
        return self.route(text, context=context, confirm=confirm, request_extensions=request_extensions)

    def bdp_handle(self, message: Dict[str, Any]) -> Dict[str, Any]:
        intent = message.get("intent")
        payload = message.get("payload", {}) if isinstance(message.get("payload", {}), dict) else {}
        if intent == "intent.router.build_plan":
            text = str(payload.get("message", ""))
            return {
                "protocol_version": "0.1",
                "message_id": new_uuid(),
                "intent": "intent_plan",
                "payload": {"intent_plan": self.analyze(text)},
            }
        return make_error("E_NO_ROUTE", "Unsupported intent for intent router", message.get("message_id"))
