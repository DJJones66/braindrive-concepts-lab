from __future__ import annotations

import json
import re
import time
from copy import deepcopy
from typing import Any, Dict, List, Mapping, Optional

from ..protocol import http_get_json, http_post_json, make_error, make_response, new_uuid, now_iso
from .base import ProtocolNode, cap


def _env_bool(env: Mapping[str, str] | None, key: str, default: bool) -> bool:
    if env is None:
        return default
    raw = str(env.get(key, str(default))).strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _env_int(env: Mapping[str, str] | None, key: str, default: int, minimum: int = 0) -> int:
    if env is None:
        return default
    try:
        value = int(str(env.get(key, str(default))).strip())
    except ValueError:
        value = default
    return max(minimum, value)


def _env_float(env: Mapping[str, str] | None, key: str, default: float, minimum: float = 0.0) -> float:
    if env is None:
        return default
    try:
        value = float(str(env.get(key, str(default))).strip())
    except ValueError:
        value = default
    return max(minimum, value)


def _env_csv(env: Mapping[str, str] | None, key: str) -> List[str]:
    if env is None:
        return []
    raw = str(env.get(key, "")).strip()
    if not raw:
        return []
    values = []
    for item in raw.split(","):
        token = item.strip()
        if token:
            values.append(token)
    return values


class WebConsoleNode(ProtocolNode):
    node_id = "node.web.console"
    node_version = "0.1.0"
    priority = 125

    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        env = ctx.env

        self.enabled = _env_bool(env, "WEBTERM_ENABLED", True)
        self.allowed_origins = {item.lower() for item in _env_csv(env, "WEBTERM_ALLOWED_ORIGINS")}
        self.idle_timeout_sec = _env_int(env, "WEBTERM_SESSION_IDLE_TIMEOUT_SEC", 900, minimum=30)
        self.max_timeout_sec = _env_int(env, "WEBTERM_SESSION_MAX_TIMEOUT_SEC", 3600, minimum=60)
        self.max_sessions_per_user = _env_int(env, "WEBTERM_MAX_CONCURRENT_SESSIONS_PER_USER", 2, minimum=1)
        self.max_message_bytes = _env_int(env, "WEBTERM_MAX_MESSAGE_BYTES", 4096, minimum=256)
        self.max_events_per_minute = _env_int(env, "WEBTERM_MAX_EVENTS_PER_MINUTE", 120, minimum=10)

        self.default_target = str((env or {}).get("WEBTERM_SSH_TARGET_DEFAULT", "node-router")).strip() or "node-router"
        self.targets = _env_csv(env, "WEBTERM_TARGETS") or [self.default_target]

        self.ssh_gateway_host = str((env or {}).get("WEBTERM_SSH_GATEWAY_HOST", "ssh-gateway")).strip()
        self.ssh_gateway_port = _env_int(env, "WEBTERM_SSH_GATEWAY_PORT", 2226, minimum=1)
        self.ssh_auth_mode = str((env or {}).get("WEBTERM_SSH_AUTH_MODE", "disabled")).strip().lower()
        self.ssh_exec_intent = str((env or {}).get("WEBTERM_SSH_EXEC_INTENT", "")).strip()
        self.environment_name = str((env or {}).get("BRAINDRIVE_ENV", "development")).strip().lower()

        self.intent_router_base_url = str((env or {}).get("WEBTERM_INTENT_ROUTER_BASE_URL", "http://intent-router-natural-language:8081")).strip().rstrip("/")
        self.router_base_url = str((env or {}).get("WEBTERM_ROUTER_BASE_URL", "http://node-router:8080")).strip().rstrip("/")
        self.http_timeout_sec = _env_float(env, "WEBTERM_HTTP_TIMEOUT_SEC", 20.0, minimum=1.0)

        loaded = self.ctx.persistence.load_state("webterm_state", {"sessions": {}})
        self.state: Dict[str, Any] = loaded if isinstance(loaded, dict) else {"sessions": {}}
        if not isinstance(self.state.get("sessions"), dict):
            self.state["sessions"] = {}

        self._validate_ssh_key_policy()

    def capabilities(self) -> List:
        return [
            cap(
                name="web.console.session.open",
                description="Open authenticated web terminal session",
                input_schema={"type": "object", "required": ["origin"]},
                risk_class="read",
                required_extensions=["identity"],
                approval_required=False,
                examples=["open web terminal for node-router"],
                idempotency="non_idempotent",
                side_effect_scope="external",
            ),
            cap(
                name="web.console.session.close",
                description="Close web terminal session",
                input_schema={"type": "object", "required": ["session_id"]},
                risk_class="read",
                required_extensions=["identity"],
                approval_required=False,
                examples=["close web terminal session"],
                idempotency="non_idempotent",
                side_effect_scope="external",
            ),
            cap(
                name="web.console.targets.list",
                description="List web terminal targets",
                input_schema={"type": "object"},
                risk_class="read",
                required_extensions=["identity"],
                approval_required=False,
                examples=["list web terminal targets"],
                idempotency="idempotent",
                side_effect_scope="none",
            ),
            cap(
                name="web.console.guides.list",
                description="List operator guides for browser terminal usage",
                input_schema={"type": "object"},
                risk_class="read",
                required_extensions=["identity"],
                approval_required=False,
                examples=["list web terminal guides"],
                idempotency="idempotent",
                side_effect_scope="none",
            ),
            cap(
                name="web.console.session.event",
                description="Process browser terminal event envelope",
                input_schema={"type": "object", "required": ["session_id", "event"]},
                risk_class="read",
                required_extensions=["identity"],
                approval_required=False,
                examples=["send terminal.input event"],
                idempotency="non_idempotent",
                side_effect_scope="external",
            ),
        ]

    def _validate_ssh_key_policy(self) -> None:
        valid_modes = {"ca_cert", "authorized_keys", "static_client_key", "disabled"}
        if self.ssh_auth_mode not in valid_modes:
            raise ValueError("WEBTERM_SSH_AUTH_MODE must be ca_cert|authorized_keys|static_client_key|disabled")

        env = self.ctx.env or {}
        client_key_file = str(env.get("WEBTERM_SSH_CLIENT_KEY_FILE", "")).strip()
        client_key_inline = str(env.get("WEBTERM_SSH_CLIENT_KEY_B64", "")).strip()

        is_dev = self.environment_name in {"dev", "development", "local", "test"}
        if not is_dev and client_key_inline and not client_key_file:
            raise ValueError("WEBTERM_SSH_CLIENT_KEY_B64 is only allowed in development when key file is absent")

    def _save(self) -> None:
        self.ctx.persistence.save_state("webterm_state", self.state)

    @staticmethod
    def _confirmation(message: Dict[str, Any]) -> Dict[str, Any]:
        extensions = message.get("extensions", {})
        if not isinstance(extensions, dict):
            return {}
        confirmation = extensions.get("confirmation", {})
        return confirmation if isinstance(confirmation, dict) else {}

    @staticmethod
    def _identity(message: Dict[str, Any]) -> Dict[str, Any]:
        extensions = message.get("extensions", {})
        if not isinstance(extensions, dict):
            return {}
        identity = extensions.get("identity", {})
        return identity if isinstance(identity, dict) else {}

    @staticmethod
    def _raw_payload(message: Dict[str, Any]) -> Dict[str, Any]:
        payload = message.get("payload", {})
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _classify_command(command: str) -> str:
        stripped = command.strip()
        if not stripped:
            return "read"

        destructive_patterns = [
            r"\brm\s+-rf\b",
            r"\bshutdown\b",
            r"\breboot\b",
            r"\bdd\b",
            r"\bmkfs\b",
            r"\bdrop\s+table\b",
        ]
        mutate_patterns = [
            r"\bgit\s+commit\b",
            r"\bgit\s+push\b",
            r"\bmv\b",
            r"\bcp\b",
            r"\btouch\b",
            r"\bmkdir\b",
            r"\btee\b",
            r"\becho\s+.+>",
            r"\bchmod\b",
            r"\bchown\b",
            r"\bsed\s+-i\b",
        ]

        lowered = stripped.lower()
        if any(re.search(pattern, lowered) for pattern in destructive_patterns):
            return "destructive"
        if any(re.search(pattern, lowered) for pattern in mutate_patterns):
            return "mutate"
        return "read"

    @staticmethod
    def _looks_like_shell_command(command: str) -> bool:
        stripped = command.strip()
        if not stripped:
            return False
        if any(token in stripped for token in ["&&", "||", " | ", ";", "`", "$("]):
            return True
        shell_heads = {
            "ls",
            "pwd",
            "cd",
            "cat",
            "grep",
            "find",
            "rg",
            "git",
            "docker",
            "python",
            "pytest",
            "curl",
            "wget",
            "echo",
            "mkdir",
            "rm",
            "mv",
            "cp",
            "chmod",
            "chown",
            "touch",
            "sed",
            "awk",
            "jq",
        }
        first = stripped.split(" ", 1)[0].strip().lower()
        return first in shell_heads

    def _guides(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "quickstart",
                "summary": "Type natural language to route through intent-router. Use slash commands for local console controls.",
            },
            {
                "name": "approvals",
                "summary": "Shell-style mutate/destructive commands require confirmation before execution.",
            },
            {
                "name": "slash-commands",
                "summary": "Use /help, /health, /targets, /use <target>, /prompts, /guide <name>, /raw on|off.",
            },
        ]

    def _help_text(self) -> str:
        return (
            "Commands:\n"
            "/help\n"
            "/health\n"
            "/targets\n"
            "/use <target>\n"
            "/prompts\n"
            "/guide <name>\n"
            "/raw on|off\n"
            "\n"
            "Any non-slash text is routed as natural language via intent-router.\n"
        )

    def _log_session(self, *, session: Dict[str, Any], event: str, reason: str) -> None:
        self.ctx.persistence.append_log(
            "webterm_sessions",
            {
                "timestamp": now_iso(),
                "trace_id": str(session.get("trace_id", "")),
                "session_id": str(session.get("session_id", "")),
                "actor_id": str(session.get("actor_id", "")),
                "source_ip": str(session.get("source_ip", "")),
                "origin": str(session.get("origin", "")),
                "target": str(session.get("target", "")),
                "event": event,
                "reason": reason,
            },
        )

    def _log_event(
        self,
        *,
        session: Dict[str, Any],
        seq: int,
        event: str,
        classification: str,
        approval_request_id: str,
        policy_decision: str,
    ) -> None:
        self.ctx.persistence.append_log(
            "webterm_events",
            {
                "timestamp": now_iso(),
                "trace_id": str(session.get("trace_id", "")),
                "session_id": str(session.get("session_id", "")),
                "seq": seq,
                "event": event,
                "classification": classification,
                "approval_request_id": approval_request_id,
                "policy_decision": policy_decision,
            },
        )

    def _log_security(self, *, session_id: str, actor_id: str, origin: str, event: str, reason: str) -> None:
        self.ctx.persistence.append_log(
            "webterm_security",
            {
                "timestamp": now_iso(),
                "trace_id": str(new_uuid()),
                "session_id": session_id,
                "actor_id": actor_id,
                "source_ip": "",
                "origin": origin,
                "target": "",
                "event": event,
                "reason": reason,
            },
        )

    def _open_sessions_for(self, actor_id: str) -> int:
        sessions = self.state.get("sessions", {})
        if not isinstance(sessions, dict):
            return 0
        return len([item for item in sessions.values() if isinstance(item, dict) and item.get("actor_id") == actor_id])

    def _get_session(self, session_id: str) -> Dict[str, Any] | None:
        sessions = self.state.get("sessions", {})
        if not isinstance(sessions, dict):
            return None
        value = sessions.get(session_id)
        return value if isinstance(value, dict) else None

    def _delete_session(self, session_id: str) -> None:
        sessions = self.state.get("sessions", {})
        if isinstance(sessions, dict):
            sessions.pop(session_id, None)
        self._save()

    def _ssh_config_error(self) -> str:
        env = self.ctx.env or {}
        # disabled is allowed for NL-first web console usage.
        if self.ssh_auth_mode == "disabled":
            return ""
        if self.ssh_auth_mode == "ca_cert":
            if not str(env.get("WEBTERM_SSH_CA_KEY_FILE", "")).strip():
                return "WEBTERM_SSH_CA_KEY_FILE is required in ca_cert mode"
        elif self.ssh_auth_mode == "authorized_keys":
            if not (
                str(env.get("WEBTERM_SSH_AUTHORIZED_KEYS_FILE", "")).strip()
                or str(env.get("WEBTERM_SSH_AUTHORIZED_KEYS_B64", "")).strip()
            ):
                return "WEBTERM_SSH_AUTHORIZED_KEYS_FILE or WEBTERM_SSH_AUTHORIZED_KEYS_B64 is required"
        elif self.ssh_auth_mode == "static_client_key":
            if not (
                str(env.get("WEBTERM_SSH_CLIENT_KEY_FILE", "")).strip()
                or str(env.get("WEBTERM_SSH_CLIENT_KEY_B64", "")).strip()
            ):
                return "WEBTERM_SSH_CLIENT_KEY_FILE or WEBTERM_SSH_CLIENT_KEY_B64 is required"
        return ""

    def _is_origin_allowed(self, origin: str) -> bool:
        lowered = origin.strip().lower()
        # If policy explicitly sets allowed origins, enforce it as-is.
        if self.allowed_origins:
            return lowered in self.allowed_origins
        if self.environment_name in {"dev", "development", "local", "test"} and (
            lowered.startswith("http://localhost")
            or lowered.startswith("https://localhost")
            or lowered.startswith("http://127.0.0.1")
            or lowered.startswith("https://127.0.0.1")
        ):
            return True
        return True

    def _actor_from_identity(self, message: Dict[str, Any]) -> tuple[str, List[str]]:
        identity = self._identity(message)
        actor_id = str(identity.get("actor_id", "")).strip()
        roles_raw = identity.get("roles", [])
        roles = [str(item).strip() for item in roles_raw if isinstance(item, str)] if isinstance(roles_raw, list) else []
        return actor_id, roles

    def _session_timed_out(self, session: Dict[str, Any]) -> tuple[bool, str]:
        now_epoch = time.time()
        created = float(session.get("created_at_epoch", now_epoch))
        last_activity = float(session.get("last_activity_epoch", now_epoch))
        if now_epoch - created > float(session.get("max_timeout_sec", self.max_timeout_sec)):
            return True, "max_timeout"
        if now_epoch - last_activity > float(session.get("idle_timeout_sec", self.idle_timeout_sec)):
            return True, "idle_timeout"
        return False, ""

    def _event_rate_allowed(self, session: Dict[str, Any]) -> bool:
        now_epoch = time.time()
        window_start = float(session.get("rate_window_start_epoch", now_epoch))
        event_count = int(session.get("rate_window_count", 0))
        if now_epoch - window_start >= 60.0:
            session["rate_window_start_epoch"] = now_epoch
            session["rate_window_count"] = 1
            return True
        if event_count >= self.max_events_per_minute:
            return False
        session["rate_window_count"] = event_count + 1
        return True

    @staticmethod
    def _prompt_for(session: Dict[str, Any]) -> str:
        target = str(session.get("target", "target"))
        folder = str(session.get("active_folder", "")).strip()
        if folder:
            return f"webterm[{target}:{folder}]$ "
        return f"webterm[{target}]$ "

    def _session_events_response(
        self,
        *,
        session: Dict[str, Any],
        events: List[Dict[str, Any]],
        classification: str = "read",
        policy_decision: str = "allowed",
        approval_request_id: str = "",
        parent_message_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        seq = int(session.get("seq", 0)) + 1
        session["seq"] = seq
        session["last_activity_epoch"] = time.time()
        self._save()
        self._log_event(
            session=session,
            seq=seq,
            event="session.events",
            classification=classification,
            approval_request_id=approval_request_id,
            policy_decision=policy_decision,
        )
        return make_response(
            "web.console.session.events",
            {
                "session_id": str(session.get("session_id", "")),
                "seq": seq,
                "events": events,
                "prompt": self._prompt_for(session),
                "classification": classification,
                "policy_decision": policy_decision,
            },
            parent_message_id,
        )

    def _execute_shell_command(self, *, session: Dict[str, Any], command: str) -> str:
        if self.ctx.route_message is not None and self.ssh_exec_intent:
            routed = self.ctx.route_message(
                {
                    "protocol_version": "0.1",
                    "message_id": new_uuid(),
                    "intent": self.ssh_exec_intent,
                    "payload": {
                        "session_id": str(session.get("session_id", "")),
                        "target": str(session.get("target", "")),
                        "command": command,
                    },
                }
            )
            if isinstance(routed, dict) and routed.get("intent") != "error":
                payload = routed.get("payload", {})
                if isinstance(payload, dict):
                    return str(payload.get("output", payload.get("text", "")))
            return "SSH backend returned an error."

        return "SSH execution is not configured. Set WEBTERM_SSH_EXEC_INTENT to enable shell command execution."

    def _health_summary(self) -> str:
        router_state = "unreachable"
        intent_state = "unreachable"
        try:
            router = http_get_json(f"{self.router_base_url}/health", timeout_sec=self.http_timeout_sec)
            if isinstance(router, dict) and router.get("ok") is True:
                router_state = f"ok (nodes={router.get('active_nodes', '?')}, caps={router.get('capability_count', '?')})"
        except Exception:
            router_state = "unreachable"

        try:
            intent = http_get_json(f"{self.intent_router_base_url}/health", timeout_sec=self.http_timeout_sec)
            if isinstance(intent, dict) and intent.get("ok") is True:
                intent_state = "ok"
        except Exception:
            intent_state = "unreachable"

        return f"router: {router_state}\nintent-router: {intent_state}\n"

    def _prompts_summary(self) -> str:
        try:
            payload = http_get_json(f"{self.router_base_url}/router/catalog", timeout_sec=self.http_timeout_sec)
        except Exception as exc:
            return f"Unable to load prompts/capabilities: {exc}\n"

        catalog = payload.get("catalog", {}) if isinstance(payload, dict) else {}
        if not isinstance(catalog, dict) or not catalog:
            return "No capabilities available.\n"

        capabilities = sorted([str(name) for name in catalog.keys()])
        by_section: Dict[str, int] = {}
        for name in capabilities:
            section = name.split(".", 1)[0]
            by_section[section] = by_section.get(section, 0) + 1

        lines = ["Capability sections:"]
        for section in sorted(by_section.keys()):
            lines.append(f"- {section}: {by_section[section]}")
        lines.append("")
        lines.append("Examples:")
        for name in capabilities[:25]:
            lines.append(f"- {name}")
        if len(capabilities) > 25:
            lines.append(f"... (+{len(capabilities) - 25} more)")
        return "\n".join(lines) + "\n"

    def _render_route_response(self, route_response: Dict[str, Any]) -> str:
        intent = str(route_response.get("intent", ""))
        payload = route_response.get("payload", {}) if isinstance(route_response.get("payload", {}), dict) else {}

        if intent == "chat.response":
            return str(payload.get("text", ""))
        if intent == "model.chat.completed":
            return str(payload.get("text", ""))
        if intent == "memory.listed":
            entries = payload.get("entries", [])
            if isinstance(entries, list):
                rows = [f"- {item.get('path', '')}" for item in entries if isinstance(item, dict)]
                return "\n".join(rows) or "No files found."
        if intent == "memory.read.result":
            return str(payload.get("content", ""))
        if intent == "folder.switched":
            folder = str(payload.get("active_folder", payload.get("folder", ""))).strip()
            return f"Active folder: {folder}" if folder else "Folder switched."
        if intent == "web.scrape.completed":
            results = payload.get("results", [])
            if isinstance(results, list) and results:
                first = results[0] if isinstance(results[0], dict) else {}
                content = first.get("content", [])
                if isinstance(content, list) and content:
                    return str(content[0])
            return "Scrape completed."

        if intent == "error":
            err = payload.get("error", {}) if isinstance(payload.get("error", {}), dict) else {}
            code = str(err.get("code", "E_NODE_ERROR"))
            message = str(err.get("message", "Unknown error"))
            return f"[{code}] {message}"

        return json.dumps(route_response, ensure_ascii=True, indent=2)

    def _route_nl_command(self, *, session: Dict[str, Any], command: str, confirm: bool) -> str:
        context: Dict[str, Any] = {
            "active_folder": str(session.get("active_folder", "")),
            "origin": str(session.get("origin", "")),
        }
        try:
            result = http_post_json(
                f"{self.intent_router_base_url}/intent/route",
                {
                    "message": command,
                    "confirm": bool(confirm),
                    "context": context,
                },
                timeout_sec=self.http_timeout_sec,
            )
        except Exception as exc:
            return f"[E_NODE_UNAVAILABLE] intent-router call failed: {exc}"

        if not isinstance(result, dict):
            return "[E_NODE_ERROR] invalid intent-router response"

        status = str(result.get("status", "")).strip().lower()
        analysis = result.get("analysis", {}) if isinstance(result.get("analysis", {}), dict) else {}

        if status == "needs_clarification":
            clarification = str(analysis.get("clarification_prompt", "I need clarification before routing this request."))
            return clarification

        route_response = result.get("route_response", {}) if isinstance(result.get("route_response", {}), dict) else {}

        if route_response.get("intent") == "folder.switched":
            payload = route_response.get("payload", {}) if isinstance(route_response.get("payload", {}), dict) else {}
            active_folder = str(payload.get("active_folder", payload.get("folder", ""))).strip()
            if active_folder:
                session["active_folder"] = active_folder

        if bool(session.get("raw_mode", False)):
            return json.dumps(result, ensure_ascii=True, indent=2)

        return self._render_route_response(route_response if route_response else result)

    def _handle_slash_command(self, *, session: Dict[str, Any], command: str, parent_message_id: str | None) -> Dict[str, Any]:
        lowered = command.strip().lower()
        if lowered == "/help":
            return self._session_events_response(
                session=session,
                events=[{"event": "terminal.output", "payload": {"data": self._help_text()}}],
                parent_message_id=parent_message_id,
            )

        if lowered == "/health":
            return self._session_events_response(
                session=session,
                events=[{"event": "terminal.output", "payload": {"data": self._health_summary()}}],
                parent_message_id=parent_message_id,
            )

        if lowered == "/targets":
            lines = "\n".join(f"- {target}" for target in self.targets)
            return self._session_events_response(
                session=session,
                events=[{"event": "terminal.output", "payload": {"data": f"Targets:\n{lines}\n"}}],
                parent_message_id=parent_message_id,
            )

        if lowered.startswith("/use "):
            selected = command.split(" ", 1)[1].strip()
            if selected not in self.targets:
                return self._session_events_response(
                    session=session,
                    events=[{"event": "terminal.output", "payload": {"data": f"Target not allowed: {selected}\n"}}],
                    policy_decision="denied",
                    parent_message_id=parent_message_id,
                )
            session["target"] = selected
            self._save()
            return self._session_events_response(
                session=session,
                events=[{"event": "terminal.output", "payload": {"data": f"Active target: {selected}\n"}}],
                parent_message_id=parent_message_id,
            )

        if lowered == "/prompts" or lowered == "/prompts next":
            return self._session_events_response(
                session=session,
                events=[{"event": "terminal.output", "payload": {"data": self._prompts_summary()}}],
                parent_message_id=parent_message_id,
            )

        if lowered.startswith("/guide"):
            parts = command.split(" ", 1)
            guide_name = parts[1].strip().lower() if len(parts) == 2 else "quickstart"
            by_name = {item["name"]: item["summary"] for item in self._guides()}
            summary = by_name.get(guide_name)
            if summary is None:
                return self._session_events_response(
                    session=session,
                    events=[{"event": "terminal.output", "payload": {"data": f"Unknown guide: {guide_name}\n"}}],
                    policy_decision="denied",
                    parent_message_id=parent_message_id,
                )
            return self._session_events_response(
                session=session,
                events=[{"event": "terminal.output", "payload": {"data": f"{guide_name}: {summary}\n"}}],
                parent_message_id=parent_message_id,
            )

        if lowered in {"/raw on", "/raw off"}:
            session["raw_mode"] = lowered.endswith("on")
            self._save()
            return self._session_events_response(
                session=session,
                events=[{"event": "terminal.output", "payload": {"data": f"raw mode {'on' if session['raw_mode'] else 'off'}\n"}}],
                parent_message_id=parent_message_id,
            )

        return self._session_events_response(
            session=session,
            events=[{"event": "terminal.output", "payload": {"data": f"Unknown command: {command}\n"}}],
            policy_decision="denied",
            parent_message_id=parent_message_id,
        )

    def _handle_terminal_command(self, *, message: Dict[str, Any], session: Dict[str, Any], command: str) -> Dict[str, Any]:
        command = command.strip()
        if not command:
            return self._session_events_response(
                session=session,
                events=[{"event": "terminal.prompt", "payload": {}}],
                parent_message_id=message.get("message_id"),
            )

        if command.startswith("/"):
            return self._handle_slash_command(session=session, command=command, parent_message_id=message.get("message_id"))

        confirmation = self._confirmation(message)
        confirmed = str(confirmation.get("status", "")).strip().lower() == "approved"

        if not self._looks_like_shell_command(command):
            output = self._route_nl_command(session=session, command=command, confirm=confirmed)
            return self._session_events_response(
                session=session,
                classification="read",
                policy_decision="allowed",
                events=[
                    {"event": "terminal.output", "payload": {"data": output + "\n"}},
                    {"event": "terminal.prompt", "payload": {}},
                ],
                parent_message_id=message.get("message_id"),
            )

        classification = self._classify_command(command)
        pending = session.get("pending_approval")
        if not isinstance(pending, dict):
            pending = {}

        if classification in {"mutate", "destructive"}:
            request_id = str(confirmation.get("request_id", "")).strip()
            pending_command = str(pending.get("command", "")).strip()
            pending_request_id = str(pending.get("request_id", "")).strip()
            if not (confirmed and request_id and pending_command == command and request_id == pending_request_id):
                approval_request_id = pending_request_id or f"appr-{new_uuid()}"
                session["pending_approval"] = {
                    "request_id": approval_request_id,
                    "command": command,
                    "classification": classification,
                    "requested_at": now_iso(),
                }
                self._save()
                self._log_event(
                    session=session,
                    seq=int(session.get("seq", 0)) + 1,
                    event="session.approval_required",
                    classification=classification,
                    approval_request_id=approval_request_id,
                    policy_decision="pending",
                )
                return make_response(
                    "web.console.session.approval_required",
                    {
                        "session_id": str(session.get("session_id", "")),
                        "command": command,
                        "classification": classification,
                        "approval_request_id": approval_request_id,
                    },
                    message.get("message_id"),
                )
            session.pop("pending_approval", None)

        output = self._execute_shell_command(session=session, command=command)
        return self._session_events_response(
            session=session,
            classification=classification,
            policy_decision="approved" if classification in {"mutate", "destructive"} else "allowed",
            events=[
                {"event": "terminal.output", "payload": {"data": output + "\n"}},
                {"event": "terminal.prompt", "payload": {}},
            ],
            parent_message_id=message.get("message_id"),
        )

    def _handle_session_open(self, message: Dict[str, Any]) -> Dict[str, Any]:
        if not self.enabled:
            return make_error("E_NODE_UNAVAILABLE", "Web terminal is disabled", message.get("message_id"))

        ssh_error = self._ssh_config_error()
        if ssh_error:
            return make_error("E_NODE_UNAVAILABLE", ssh_error, message.get("message_id"))

        payload = self._raw_payload(message)
        actor_id, roles = self._actor_from_identity(message)
        if not actor_id:
            return make_error("E_WEBTERM_AUTH_REQUIRED", "Identity extension is required", message.get("message_id"))

        origin = str(payload.get("origin", "")).strip()
        if not origin:
            return make_error("E_BAD_MESSAGE", "origin is required", message.get("message_id"))
        if not self._is_origin_allowed(origin):
            self._log_security(session_id="", actor_id=actor_id, origin=origin, event="origin.denied", reason="origin_not_allowed")
            return make_error("E_WEBTERM_ORIGIN_DENIED", "Origin denied", message.get("message_id"))

        if self._open_sessions_for(actor_id) >= self.max_sessions_per_user:
            return make_error("E_WEBTERM_POLICY_DENIED", "Session limit reached for actor", message.get("message_id"))

        target = str(payload.get("target", self.default_target)).strip() or self.default_target
        if target not in self.targets:
            return make_error("E_WEBTERM_POLICY_DENIED", f"Target denied: {target}", message.get("message_id"))

        now_epoch = time.time()
        session_id = f"sess_{new_uuid()}"
        session = {
            "session_id": session_id,
            "trace_id": str(new_uuid()),
            "actor_id": actor_id,
            "roles": roles,
            "source_ip": str(payload.get("source_ip", "")),
            "origin": origin,
            "target": target,
            "active_folder": "",
            "raw_mode": False,
            "seq": 0,
            "created_at": now_iso(),
            "created_at_epoch": now_epoch,
            "last_activity_epoch": now_epoch,
            "idle_timeout_sec": self.idle_timeout_sec,
            "max_timeout_sec": self.max_timeout_sec,
            "rate_window_start_epoch": now_epoch,
            "rate_window_count": 0,
        }
        self.state.setdefault("sessions", {})[session_id] = session
        self._save()
        self._log_session(session=session, event="session.opened", reason="ok")

        return make_response(
            "web.console.session.ready",
            {
                "session_id": session_id,
                "target": target,
                "idle_timeout_sec": self.idle_timeout_sec,
                "max_timeout_sec": self.max_timeout_sec,
                "gateway": {"host": self.ssh_gateway_host, "port": self.ssh_gateway_port},
                "prompt": self._prompt_for(session),
                "banner": "BrainDrive Web Terminal Ready",
                "nl_routing_enabled": True,
            },
            message.get("message_id"),
        )

    def _handle_session_close(self, message: Dict[str, Any]) -> Dict[str, Any]:
        payload = self._raw_payload(message)
        actor_id, _ = self._actor_from_identity(message)
        session_id = str(payload.get("session_id", "")).strip()
        if not session_id:
            return make_error("E_BAD_MESSAGE", "session_id is required", message.get("message_id"))
        session = self._get_session(session_id)
        if session is None:
            return make_error("E_WEBTERM_SESSION_EXPIRED", "Session not found", message.get("message_id"))
        if actor_id and actor_id != str(session.get("actor_id", "")):
            return make_error("E_WEBTERM_POLICY_DENIED", "Session actor mismatch", message.get("message_id"))
        self._delete_session(session_id)
        self._log_session(session=session, event="session.closed", reason=str(payload.get("reason", "requested")))
        return make_response("web.console.session.closed", {"session_id": session_id}, message.get("message_id"))

    def _handle_session_event(self, message: Dict[str, Any]) -> Dict[str, Any]:
        payload = self._raw_payload(message)
        actor_id, _ = self._actor_from_identity(message)
        session_id = str(payload.get("session_id", "")).strip()
        event_name = str(payload.get("event", "")).strip()

        if not session_id:
            return make_error("E_BAD_MESSAGE", "session_id is required", message.get("message_id"))
        if not event_name:
            return make_error("E_BAD_MESSAGE", "event is required", message.get("message_id"))

        session = self._get_session(session_id)
        if session is None:
            return make_error("E_WEBTERM_SESSION_EXPIRED", "Session not found", message.get("message_id"))
        if actor_id and actor_id != str(session.get("actor_id", "")):
            return make_error("E_WEBTERM_POLICY_DENIED", "Session actor mismatch", message.get("message_id"))

        timed_out, reason = self._session_timed_out(session)
        if timed_out:
            self._delete_session(session_id)
            self._log_session(session=session, event="session.closed", reason=reason)
            return make_error("E_WEBTERM_SESSION_EXPIRED", "Session expired", message.get("message_id"))

        if not self._event_rate_allowed(session):
            self._save()
            return make_error("E_WEBTERM_POLICY_DENIED", "Rate limit exceeded", message.get("message_id"))

        event_payload = payload.get("payload", {})
        if not isinstance(event_payload, dict):
            event_payload = {}

        if event_name == "session.ping":
            return self._session_events_response(
                session=session,
                events=[{"event": "session.ready", "payload": {"session_id": session_id, "pong": True}}],
                parent_message_id=message.get("message_id"),
            )

        if event_name == "terminal.resize":
            cols = int(event_payload.get("cols", 80))
            rows = int(event_payload.get("rows", 24))
            session["cols"] = max(20, cols)
            session["rows"] = max(10, rows)
            return self._session_events_response(
                session=session,
                events=[{"event": "terminal.output", "payload": {"data": f"resized to {session['cols']}x{session['rows']}\n"}}],
                parent_message_id=message.get("message_id"),
            )

        if event_name == "session.close":
            return self._handle_session_close(
                {
                    "message_id": message.get("message_id"),
                    "extensions": message.get("extensions", {}),
                    "payload": {"session_id": session_id, "reason": "client_close"},
                }
            )

        if event_name not in {"terminal.input", "session.command"}:
            return make_error("E_BAD_MESSAGE", f"Unsupported event: {event_name}", message.get("message_id"))

        if event_name == "terminal.input":
            command = str(event_payload.get("data", ""))
        else:
            command = str(event_payload.get("command", ""))

        if len(command.encode("utf-8")) > self.max_message_bytes:
            return make_error("E_WEBTERM_POLICY_DENIED", "Message exceeds maximum size", message.get("message_id"))

        return self._handle_terminal_command(message=message, session=session, command=command)

    def handle(self, message: Dict[str, Any]) -> Dict[str, Any]:
        intent = str(message.get("intent", ""))
        payload = self._raw_payload(message)
        if not isinstance(payload, dict):
            return make_error("E_BAD_MESSAGE", "payload must be object", message.get("message_id"))

        if intent == "web.console.targets.list":
            actor_id, _ = self._actor_from_identity(message)
            if not actor_id:
                return make_error("E_WEBTERM_AUTH_REQUIRED", "Identity extension is required", message.get("message_id"))
            return make_response(
                "web.console.targets",
                {
                    "targets": deepcopy(self.targets),
                    "default_target": self.default_target,
                },
                message.get("message_id"),
            )

        if intent == "web.console.guides.list":
            actor_id, _ = self._actor_from_identity(message)
            if not actor_id:
                return make_error("E_WEBTERM_AUTH_REQUIRED", "Identity extension is required", message.get("message_id"))
            return make_response("web.console.guides", {"guides": self._guides()}, message.get("message_id"))

        if intent == "web.console.session.open":
            return self._handle_session_open(message)

        if intent == "web.console.session.close":
            return self._handle_session_close(message)

        if intent == "web.console.session.event":
            return self._handle_session_event(message)

        return make_error("E_NO_ROUTE", "Unsupported intent", message.get("message_id"))
