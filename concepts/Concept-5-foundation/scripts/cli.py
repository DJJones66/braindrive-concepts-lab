#!/usr/bin/env python3
from __future__ import annotations

import argparse
import atexit
from datetime import datetime, timezone
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional
from urllib import error, request


def _load_local_dotenv() -> None:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    try:
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if not key:
                continue
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                value = value[1:-1]
            os.environ.setdefault(key, value)
    except Exception:
        # Ignore malformed .env lines and continue with process env/defaults.
        return


_load_local_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _env(*names: str, default: str) -> str:
    for name in names:
        value = os.getenv(name)
        if value is not None:
            return value
    return default


def _env_bool(*names: str, default: bool) -> bool:
    raw_default = "true" if default else "false"
    value = _env(*names, default=raw_default).strip().lower()
    return value in {"1", "true", "on", "yes"}


DEFAULT_ROUTER_BASE = _env("BRAINDRIVE_ROUTER_BASE", default="http://localhost:9480")
DEFAULT_INTENT_BASE = _env("BRAINDRIVE_INTENT_BASE", default="http://localhost:9481")
DEFAULT_GATEWAY_BASE = _env("BRAINDRIVE_GATEWAY_BASE", default="http://localhost:9482")
DEFAULT_TIMEOUT_SEC = float(_env("BRAINDRIVE_CLI_TIMEOUT_SEC", default="8.0"))
DEFAULT_HISTORY_FILE = (Path(__file__).resolve().parent.parent / "data" / "runtime" / "state" / ".cli_history").as_posix()
DEFAULT_LIBRARY_ROOT = (Path(__file__).resolve().parent.parent / "data" / "library").as_posix()
DEFAULT_HISTORY_MAX = int(_env("BRAINDRIVE_CLI_HISTORY_MAX", default="2000"))
DEFAULT_PROMPTS_PAGE_SIZE = int(_env("BRAINDRIVE_PROMPTS_PAGE_SIZE", default="14"))
ANSI_BLUE = "\033[34m"
ANSI_GREEN = "\033[32m"
ANSI_RED = "\033[31m"
ANSI_CYAN = "\033[36m"
ANSI_MAGENTA = "\033[35m"
ANSI_RESET = "\033[0m"
ANSI_COLOR_MAP = {
    "black": "\033[30m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "blue": "\033[34m",
    "magenta": "\033[35m",
    "cyan": "\033[36m",
    "white": "\033[37m",
    "bright_black": "\033[90m",
    "bright_red": "\033[91m",
    "bright_green": "\033[92m",
    "bright_yellow": "\033[93m",
    "bright_blue": "\033[94m",
    "bright_magenta": "\033[95m",
    "bright_cyan": "\033[96m",
    "bright_white": "\033[97m",
}


def _resolve_cli_color(*var_names: str, default: str) -> str:
    raw = _env(*var_names, default=default).strip().lower().replace("-", "_")
    return ANSI_COLOR_MAP.get(raw, ANSI_COLOR_MAP[default])


ANSI_SYSTEM = _resolve_cli_color("BRAINDRIVE_CLI_COLOR_SYSTEM", default="cyan")
ANSI_AI = _resolve_cli_color("BRAINDRIVE_CLI_COLOR_AI", default="green")
ANSI_BANNER = _resolve_cli_color("BRAINDRIVE_CLI_COLOR_BANNER", default="blue")
ANSI_VERSION = _resolve_cli_color("BRAINDRIVE_CLI_COLOR_VERSION", default="red")
ANSI_PROMPT_APP = _resolve_cli_color(
    "BRAINDRIVE_CLI_COLOR_PROMPT_APP",
    "BRAINDRIVE_CLI_COLOR_USER",
    default="blue",
)
ANSI_PROMPT_FOLDER = _resolve_cli_color(
    "BRAINDRIVE_CLI_COLOR_PROMPT_FOLDER",
    "BRAINDRIVE_CLI_COLOR_USER",
    default="green",
)
ANSI_PROMPT_ARROW = _resolve_cli_color(
    "BRAINDRIVE_CLI_COLOR_PROMPT_ARROW",
    "BRAINDRIVE_CLI_COLOR_USER",
    default="blue",
)
ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")
_READLINE_ACTIVE = False


def _colorize(text: str, color: str, use_color: bool) -> str:
    if not use_color:
        return text
    return f"{color}{text}{ANSI_RESET}"


def _request(method: str, url: str, timeout_sec: float, payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = request.Request(url=url, data=body, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=timeout_sec) as resp:
            raw = resp.read().decode("utf-8")
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} {url}: {raw}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"request failed for {url}: {exc}") from exc

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid JSON from {url}: {raw[:300]}") from exc

    if not isinstance(parsed, dict):
        raise RuntimeError(f"response from {url} is not a JSON object")
    return parsed


def _wait_for_health(base_url: str, timeout_sec: float, label: str, attempts: int = 40) -> None:
    use_color = _should_use_color()
    for _ in range(attempts):
        try:
            body = _request("GET", f"{base_url}/health", timeout_sec=timeout_sec)
            if body.get("ok"):
                print(_colorize(f"[ok] {label} healthy", ANSI_SYSTEM, use_color))
                return
        except Exception:
            pass
        time.sleep(0.5)
    raise RuntimeError(f"timed out waiting for {label} health endpoint")


def _setup_line_editing() -> None:
    global _READLINE_ACTIVE
    try:
        import readline
    except Exception:
        return

    history_file = Path(_env("BRAINDRIVE_CLI_HISTORY_FILE", default=DEFAULT_HISTORY_FILE)).expanduser()
    try:
        history_file.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        return

    commands = [
        "/help",
        "/health",
        "/commands",
        "/commands folder",
        "/prompts",
        "/prompts workflow",
        "/prompts memory",
        "/prompts model",
        "/prompts next",
        "/clear",
        "/raw on",
        "/raw off",
        "/exit",
        "/quit",
    ]

    def _complete(text: str, state: int) -> Optional[str]:
        options = [cmd for cmd in commands if cmd.startswith(text)]
        if state < len(options):
            return options[state]
        return None

    try:
        readline.parse_and_bind("set editing-mode emacs")
        readline.parse_and_bind("set enable-keypad on")
        readline.parse_and_bind("tab: complete")
        readline.set_completer(_complete)
        readline.set_history_length(DEFAULT_HISTORY_MAX)
        if hasattr(readline, "set_auto_history"):
            readline.set_auto_history(True)
        if history_file.exists():
            readline.read_history_file(str(history_file))
    except Exception:
        return

    _READLINE_ACTIVE = True

    def _persist_history() -> None:
        try:
            readline.write_history_file(str(history_file))
        except Exception:
            return

    atexit.register(_persist_history)


def _should_use_color() -> bool:
    mode = _env("BRAINDRIVE_CLI_COLOR", default="auto").strip().lower()
    if mode in {"0", "false", "off", "no"}:
        return False
    if mode in {"1", "true", "on", "yes", "always"}:
        return True
    if os.getenv("NO_COLOR") is not None:
        return False
    if not sys.stdout.isatty():
        return False
    term = os.getenv("TERM", "").strip().lower()
    if not term or term == "dumb":
        return False
    return True


def _readline_safe_prompt(text: str) -> str:
    if not _READLINE_ACTIVE or "\x1b[" not in text:
        return text
    # Tell GNU readline which bytes are non-printing so cursor math and wrapping stay correct.
    return ANSI_ESCAPE_RE.sub(lambda match: f"\001{match.group(0)}\002", text)


def _print_banner(use_color: bool) -> None:
    lines = [
        " ____             _       ____       _           ",
        "| __ ) _ __ __ _(_)_ __ |  _ \\ _ __(_)_   _____ ",
        "|  _ \\| '__/ _` | | '_ \\| | | | '__| \\ \\ / / _ \\",
        "| |_) | | | (_| | | | | | |_| | |  | |\\ V /  __/",
        "|____/|_|  \\__,_|_|_| |_|____/|_|  |_| \\_/ \\___|",
    ]
    if use_color:
        for line in lines[:-1]:
            print(_colorize(line, ANSI_BANNER, use_color))
        print(f"{_colorize(lines[-1], ANSI_BANNER, use_color)} {_colorize('v0.1', ANSI_VERSION, use_color)}")
    else:
        for line in lines[:-1]:
            print(line)
        print(f"{lines[-1]} v0.1")


def _clear_screen() -> None:
    if os.name == "nt":
        os.system("cls")
        return
    if sys.stdout.isatty():
        # ANSI clear + cursor home for interactive terminals.
        print("\033[2J\033[H", end="", flush=True)
        return
    print("")


class CliClient:
    def __init__(
        self,
        router_base: str,
        intent_base: str,
        gateway_base: str,
        timeout_sec: float,
        raw_output: bool = False,
    ) -> None:
        self.router_base = router_base.rstrip("/")
        self.intent_base = intent_base.rstrip("/")
        self.gateway_base = gateway_base.rstrip("/")
        self.timeout_sec = timeout_sec
        self.raw_output = raw_output
        self.stream_model_chat = _env_bool(
            "BRAINDRIVE_CLI_STREAM_MODEL_CHAT",
            default=True,
        )
        self.stream_fallback_only = _env_bool(
            "BRAINDRIVE_CLI_STREAM_FALLBACK_ONLY",
            default=True,
        )
        self.stream_diagnostics = _env_bool(
            "BRAINDRIVE_CLI_STREAM_DIAGNOSTICS",
            default=False,
        )
        self.allow_intent_fallback = _env_bool(
            "BRAINDRIVE_CLI_ALLOW_INTENT_FALLBACK",
            default=False,
        )
        self.actor_id = _env("BRAINDRIVE_CLI_ACTOR_ID", default="cli.user")
        self.actor_roles = [item.strip() for item in _env("BRAINDRIVE_CLI_ACTOR_ROLES", default="operator").split(",") if item.strip()]
        if not self.actor_roles:
            self.actor_roles = ["operator"]
        self.library_root = Path(_env("BRAINDRIVE_LIBRARY_ROOT", default=DEFAULT_LIBRARY_ROOT)).expanduser()
        self.conversation_id = ""
        self.active_folder = ""
        self.use_color = _should_use_color()
        self.prompts_page_size = max(1, DEFAULT_PROMPTS_PAGE_SIZE)
        self._prompt_lines: List[str] = []
        self._prompt_cursor = 0
        self._prompt_title = ""

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _ensure_conversation_id(self) -> str:
        if not self.conversation_id:
            self.conversation_id = f"conv_cli_{uuid.uuid4()}"
        return self.conversation_id

    @staticmethod
    def _safe_conversation_filename(conversation_id: str) -> str:
        raw = str(conversation_id).strip()
        safe = "".join(ch for ch in raw if ch.isalnum() or ch in {"-", "_"})
        return safe or f"conv_{uuid.uuid4()}"

    def _chat_paths(self, conversation_id: str) -> tuple[Path, Path]:
        chats_dir = self.library_root.resolve() / "chats"
        chats_dir.mkdir(parents=True, exist_ok=True)
        name = self._safe_conversation_filename(conversation_id)
        return chats_dir / f"{name}.jsonl", chats_dir / f"{name}.meta.json"

    def _load_provider_history_messages(self, conversation_id: str, max_turns: int, max_chars: int) -> List[Dict[str, str]]:
        jsonl_path, _ = self._chat_paths(conversation_id)
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
            content = str(item.get("content", "")).strip()
            if not content:
                continue
            if used + len(content) > max_chars:
                break
            bounded.append(item)
            used += len(content)
        bounded.reverse()
        return bounded

    def _append_stream_chat_record(self, *, input_text: str, output_text: str, complete: bool) -> None:
        conversation_id = self._ensure_conversation_id()
        jsonl_path, meta_path = self._chat_paths(conversation_id)
        record_id = f"msg_{uuid.uuid4()}"
        trace_id = str(uuid.uuid4())

        chat_record = {
            "ts": self._now_iso(),
            "conversation_id": conversation_id,
            "record_id": record_id,
            "actor": {
                "id": self.actor_id,
                "type": "user",
            },
            "channel": "cli",
            "route": {
                "intent": "model.chat.completed",
                "status": "streamed" if complete else "streamed_partial",
            },
            "input": {"text": input_text},
            "output": {"intent": "model.chat.completed", "text": output_text},
            "metadata": {
                "channel": "cli",
                "source": "cli_direct_stream",
            },
            "trace": {
                "trace_id": trace_id,
                "auth_session_id": "",
                "console_session_id": "",
            },
        }

        with jsonl_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(chat_record, ensure_ascii=True) + "\n")

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
        sidecar["updated_at"] = chat_record["ts"]
        sidecar["last_record_id"] = record_id
        meta_path.write_text(json.dumps(sidecar, ensure_ascii=True, indent=2), encoding="utf-8")

    def prompt(self) -> str:
        app_label = "braindrive"
        arrow = "> "
        if self.use_color:
            app_label = _colorize("braindrive", ANSI_PROMPT_APP, self.use_color)
            arrow = _colorize("> ", ANSI_PROMPT_ARROW, self.use_color)
        if self.active_folder:
            folder_label = self.active_folder
            if self.use_color:
                folder_label = _colorize(self.active_folder, ANSI_PROMPT_FOLDER, self.use_color)
            return f"{app_label} [{folder_label}]{arrow}"
        return f"{app_label}{arrow}"

    def _print_system(self, text: str) -> None:
        print(_colorize(text, ANSI_SYSTEM, self.use_color))

    def _print_ai(self, text: str) -> None:
        print(_colorize(text, ANSI_AI, self.use_color))

    @staticmethod
    def _extract_route_response(result: Dict[str, Any]) -> Dict[str, Any]:
        route_response = result.get("route_response")
        if isinstance(route_response, dict):
            return route_response
        if isinstance(result.get("intent"), str) and isinstance(result.get("payload"), dict):
            return result
        return {}

    def _track_active_folder(self, result: Dict[str, Any]) -> None:
        response = self._extract_route_response(result)
        if not response:
            return
        intent = str(response.get("intent", ""))
        payload = response.get("payload", {})
        if not isinstance(payload, dict):
            return
        if intent in {"folder.listed", "folder.switched"}:
            active = payload.get("active_folder", "")
            if isinstance(active, str):
                self.active_folder = active.strip()

    @staticmethod
    def _error_details(response: Dict[str, Any]) -> tuple[str, str]:
        payload = response.get("payload", {})
        if not isinstance(payload, dict):
            return "", ""
        err = payload.get("error", {})
        if not isinstance(err, dict):
            return "", ""
        code = str(err.get("code", "")).strip()
        message = str(err.get("message", "")).strip()
        return code, message

    def _route_bdp_with_retry(
        self,
        intent: str,
        payload: Dict[str, Any],
        *,
        retry_codes: set[str],
        attempts: int = 20,
        delay_sec: float = 0.5,
    ) -> Dict[str, Any]:
        last_error = "unknown error"
        for _ in range(max(1, attempts)):
            try:
                response = self.route_bdp(intent, payload)
            except Exception as exc:
                last_error = str(exc)
                time.sleep(max(0.0, delay_sec))
                continue

            if response.get("intent") != "error":
                return response

            code, message = self._error_details(response)
            if code in retry_codes:
                last_error = f"{code}: {message or 'retryable route error'}"
                time.sleep(max(0.0, delay_sec))
                continue
            return response

        raise RuntimeError(last_error)

    def refresh_active_folder(self) -> None:
        try:
            listed = self.route_bdp("folder.list", {})
        except Exception:
            return
        self._track_active_folder(listed)

    def route_bdp(self, intent: str, payload: Dict[str, Any], extensions: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        message: Dict[str, Any] = {
            "protocol_version": "0.1",
            "message_id": str(uuid.uuid4()),
            "intent": intent,
            "payload": payload,
        }
        if extensions is not None:
            message["extensions"] = extensions
        return _request("POST", f"{self.router_base}/route", timeout_sec=self.timeout_sec, payload=message)

    def route_text(
        self,
        text: str,
        confirm: bool = False,
        extensions: Optional[Dict[str, Any]] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        self._ensure_conversation_id()
        payload: Dict[str, Any] = {
            "message": text,
            "confirm": bool(confirm),
        }
        if extensions:
            payload["extensions"] = extensions
        if context:
            payload["context"] = context

        gateway_extensions = dict(payload.get("extensions", {})) if isinstance(payload.get("extensions"), dict) else {}
        gateway_extensions["identity"] = {
            "actor_id": self.actor_id,
            "roles": self.actor_roles,
            "actor_type": "user",
        }
        gateway_payload: Dict[str, Any] = {
            "conversation_id": self.conversation_id,
            "message": text,
            "confirm": bool(confirm),
            "context": payload.get("context", {}),
            "extensions": gateway_extensions,
            "metadata": {
                "channel": "cli",
            },
        }
        try:
            gateway_result = _request(
                "POST",
                f"{self.gateway_base}/api/v1/messages",
                timeout_sec=self.timeout_sec,
                payload=gateway_payload,
            )
            if gateway_result.get("ok") is not True:
                err = gateway_result.get("error", {}) if isinstance(gateway_result.get("error"), dict) else {}
                message = str(err.get("message", "gateway route failed")).strip() or "gateway route failed"
                raise RuntimeError(message)
            returned_conversation_id = str(gateway_result.get("conversation_id", "")).strip()
            if returned_conversation_id:
                self.conversation_id = returned_conversation_id
            return gateway_result
        except Exception:
            if not self.allow_intent_fallback:
                raise
            return _request("POST", f"{self.intent_base}/intent/route", timeout_sec=self.timeout_sec, payload=payload)

    def analyze_text(self, text: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"message": text}
        if context:
            payload["context"] = context
        body = _request("POST", f"{self.intent_base}/intent/analyze", timeout_sec=self.timeout_sec, payload=payload)
        analysis = body.get("analysis", {})
        if body.get("ok") is not True or not isinstance(analysis, dict):
            raise RuntimeError("intent analysis unavailable")
        return analysis

    def router_catalog(self) -> Dict[str, Any]:
        body = _request("GET", f"{self.router_base}/router/catalog", timeout_sec=self.timeout_sec)
        if body.get("ok") is not True or not isinstance(body.get("catalog"), dict):
            raise RuntimeError("router catalog unavailable")
        return body["catalog"]

    def router_registry(self) -> List[Dict[str, Any]]:
        body = _request("GET", f"{self.router_base}/router/registry", timeout_sec=self.timeout_sec)
        if body.get("ok") is not True or not isinstance(body.get("nodes"), list):
            raise RuntimeError("router registry unavailable")
        return [item for item in body["nodes"] if isinstance(item, dict)]

    @staticmethod
    def _dedupe_strings(values: List[str]) -> List[str]:
        out: List[str] = []
        seen: set[str] = set()
        for value in values:
            item = value.strip()
            if not item or item in seen:
                continue
            seen.add(item)
            out.append(item)
        return out

    @staticmethod
    def _is_internal_visibility(visibility: Any) -> bool:
        return str(visibility).strip().lower() == "internal"

    @classmethod
    def _is_user_facing_discovery_capability(cls, capability: str, visibility: Any = "") -> bool:
        if cls._is_internal_visibility(visibility):
            return False
        # Fallback until all capabilities carry explicit visibility metadata.
        if capability.startswith("session.") and str(visibility).strip() == "":
            return False
        return True

    def _load_prompt_specs(self) -> Dict[str, Dict[str, Any]]:
        specs: Dict[str, Dict[str, Any]] = {}
        try:
            nodes = self.router_registry()
            for node in nodes:
                capabilities = node.get("capabilities", [])
                if not isinstance(capabilities, list):
                    continue
                for capability in capabilities:
                    if not isinstance(capability, dict):
                        continue
                    name = capability.get("name")
                    if not isinstance(name, str) or not name.strip():
                        continue
                    if not self._is_user_facing_discovery_capability(name, capability.get("visibility", "")):
                        continue
                    spec = specs.setdefault(name, {"examples": [], "descriptions": [], "required_fields": []})

                    description = capability.get("description")
                    if isinstance(description, str) and description.strip():
                        spec["descriptions"].append(description.strip())

                    input_schema = capability.get("input_schema", {})
                    if isinstance(input_schema, dict):
                        required_fields = input_schema.get("required", [])
                        if isinstance(required_fields, list):
                            for field in required_fields:
                                if isinstance(field, str) and field.strip():
                                    spec["required_fields"].append(field.strip())

                    values = capability.get("examples", [])
                    if isinstance(values, list):
                        for value in values:
                            if isinstance(value, str):
                                spec["examples"].append(value)
        except Exception:
            pass

        if not specs:
            # Fallback: still provide capability names if registry details are unavailable.
            catalog = self.router_catalog()
            for capability, entries in catalog.items():
                if not isinstance(capability, str) or not capability.strip():
                    continue
                visibility = ""
                if isinstance(entries, list):
                    for item in entries:
                        if not isinstance(item, dict):
                            continue
                        if self._is_internal_visibility(item.get("visibility", "")):
                            visibility = "internal"
                            break
                        if not visibility and str(item.get("visibility", "")).strip():
                            visibility = str(item.get("visibility", "")).strip()
                if not self._is_user_facing_discovery_capability(capability, visibility):
                    continue
                specs[capability] = {"examples": [], "descriptions": [], "required_fields": []}

        normalized: Dict[str, Dict[str, Any]] = {}
        for capability in sorted(specs.keys()):
            details = specs.get(capability, {})
            normalized[capability] = {
                "examples": self._dedupe_strings(
                    [value for value in details.get("examples", []) if isinstance(value, str)]
                ),
                "descriptions": self._dedupe_strings(
                    [value for value in details.get("descriptions", []) if isinstance(value, str)]
                ),
                "required_fields": self._dedupe_strings(
                    [value for value in details.get("required_fields", []) if isinstance(value, str)]
                ),
            }
        return normalized

    @staticmethod
    def _group_prompt_specs(specs: Dict[str, Dict[str, Any]]) -> Dict[str, List[str]]:
        grouped: Dict[str, List[str]] = {}
        for capability in sorted(specs.keys()):
            section = capability.split(".", 1)[0]
            grouped.setdefault(section, []).append(capability)
        return grouped

    def _reset_prompt_pager(self) -> None:
        self._prompt_lines = []
        self._prompt_cursor = 0
        self._prompt_title = ""

    def _start_prompt_pager(self, title: str, lines: List[str]) -> None:
        self._prompt_title = title
        self._prompt_lines = lines
        self._prompt_cursor = 0
        self.print_prompts_next()

    def print_prompts_next(self) -> None:
        if not self._prompt_lines:
            self._print_system("[prompts] no active paged output. Use '/prompts' first.")
            return

        total = len(self._prompt_lines)
        size = self.prompts_page_size
        start = self._prompt_cursor
        end = min(start + size, total)
        page = (start // size) + 1
        pages = ((total - 1) // size) + 1

        self._print_system(f"[prompts] {self._prompt_title} (page {page}/{pages})")
        for line in self._prompt_lines[start:end]:
            self._print_system(line)

        self._prompt_cursor = end
        if end < total:
            remaining = total - end
            self._print_system(f"[prompts] {remaining} more lines. Use '/prompts next' to continue.")
        else:
            self._print_system("[prompts] end.")

    @staticmethod
    def _render_prompts_section(
        *,
        section: str,
        grouped: Dict[str, List[str]],
        specs: Dict[str, Dict[str, Any]],
    ) -> List[str]:
        lines: List[str] = [f"{section}:"]
        for capability in grouped.get(section, []):
            details = specs.get(capability, {})
            examples = details.get("examples", [])
            descriptions = details.get("descriptions", [])

            lines.append(f"- {capability}")
            if descriptions:
                lines.append(f"  = {descriptions[0]}")
            if examples:
                for sample in examples:
                    lines.append(f"  > {sample}")
            else:
                lines.append("  > (no examples provided by node metadata)")
        return lines

    def _render_prompts_index(self, grouped: Dict[str, List[str]]) -> List[str]:
        lines: List[str] = ["Sections:"]
        for section in sorted(grouped.keys()):
            lines.append(f"- {section} ({len(grouped[section])} capabilities)")
        lines.append("")
        lines.append("Usage:")
        lines.append("- /prompts <section>    Show section details")
        lines.append("- /prompts next         Continue paged output")
        lines.append("- /prompts all          Show all sections (paged)")
        lines.append("- /prompts workflow     Example section command")
        return lines

    def handle_prompts_command(self, command_arg: str = "") -> None:
        arg = command_arg.strip().lower()
        if arg in {"next", "continue", "more"}:
            self.print_prompts_next()
            return

        specs = self._load_prompt_specs()
        if not specs:
            self._reset_prompt_pager()
            self._print_system("[prompts] no capabilities discovered")
            return

        grouped = self._group_prompt_specs(specs)
        if not arg:
            self._start_prompt_pager("sections", self._render_prompts_index(grouped))
            return

        if arg == "all":
            lines: List[str] = []
            for section in sorted(grouped.keys()):
                lines.extend(self._render_prompts_section(section=section, grouped=grouped, specs=specs))
                lines.append("")
            if "model.chat.complete" in specs:
                lines.append("fallback:")
                lines.append("- Any other normal sentence will route to model chat.")
            self._start_prompt_pager("all sections", lines)
            return

        if arg not in grouped:
            available = ", ".join(sorted(grouped.keys()))
            self._reset_prompt_pager()
            self._print_system(f"[prompts] unknown section: {arg}")
            self._print_system(f"[prompts] available: {available}")
            self._print_system("[prompts] use '/prompts' to list sections")
            return

        lines = self._render_prompts_section(section=arg, grouped=grouped, specs=specs)
        if "model.chat.complete" in specs and arg == "model":
            lines.append("")
            lines.append("fallback:")
            lines.append("- Any other normal sentence will route to model chat.")
        self._start_prompt_pager(f"section '{arg}'", lines)

    @staticmethod
    def _usage_from_example(example: str, placeholders: List[str]) -> str:
        if not placeholders:
            return ""
        primary = placeholders[0]
        patterns = [
            r"^(.*\bfor\s+).+$",
            r"^(.*\bto\s+).+$",
            r"^(.*\bnamed\s+).+$",
            r"^(.*\bcalled\s+).+$",
            r"^(.*\babout\s+).+$",
        ]
        for pattern in patterns:
            match = re.match(pattern, example, flags=re.IGNORECASE)
            if match:
                return f"{match.group(1)}{primary}"

        words = example.split()
        if len(words) >= 2:
            words[-1] = primary
            return " ".join(words)
        return ""

    @classmethod
    def _capability_usage_hint(cls, capability: str, details: Dict[str, Any]) -> str:
        examples = details.get("examples", [])
        if not isinstance(examples, list):
            examples = []
        required = details.get("required_fields", [])
        placeholders = [f"<{value}>" for value in required if isinstance(value, str) and value.strip()]

        for example in examples:
            if not isinstance(example, str):
                continue
            trimmed = example.strip()
            if not trimmed:
                continue
            usage = cls._usage_from_example(trimmed, placeholders)
            if usage:
                return usage
            return trimmed

        if placeholders:
            return f"{capability} {' '.join(placeholders)}"
        return capability

    def handle_commands_search(self, command_arg: str = "") -> None:
        query = command_arg.strip().lower()
        if not query:
            self._print_system("[commands] usage: /commands <word>")
            return

        specs = self._load_prompt_specs()
        if not specs:
            self._print_system("[commands] no capability metadata available")
            return

        prompt_matches: List[Dict[str, str]] = []
        capability_matches: List[str] = []

        for capability in sorted(specs.keys()):
            details = specs.get(capability, {})
            descriptions = details.get("descriptions", [])
            examples = details.get("examples", [])

            matched = False
            if query in capability.lower():
                matched = True
            else:
                for description in descriptions:
                    if query in description.lower():
                        matched = True
                        break

            if matched:
                capability_matches.append(capability)

            for example in examples:
                if query in example.lower():
                    prompt_matches.append({"capability": capability, "example": example})

        deduped_matches: List[Dict[str, str]] = []
        seen_examples: set[str] = set()
        for item in prompt_matches:
            example = item.get("example", "").strip()
            if not example or example in seen_examples:
                continue
            seen_examples.add(example)
            deduped_matches.append(item)
        prompt_matches = deduped_matches
        capability_matches = self._dedupe_strings(capability_matches)

        if not prompt_matches and not capability_matches:
            self._print_system(f"[commands] no prompt matches for '{query}'")
            return

        if prompt_matches:
            self._print_system(f"[commands] prompt matches for '{query}':")
            for item in prompt_matches:
                example = str(item.get("example", "")).strip()
                capability = str(item.get("capability", "")).strip()
                self._print_system(f"- {example}")
                details = specs.get(capability, {})
                required = details.get("required_fields", [])
                placeholders = [f"<{value}>" for value in required if isinstance(value, str) and value.strip()]
                usage = self._usage_from_example(example, placeholders)
                if usage and usage.lower() != example.lower():
                    self._print_system(f"  usage: {usage} (replace example value)")
                elif placeholders:
                    self._print_system(f"  required values: {', '.join(placeholders)}")

        prompt_capabilities = {str(item.get("capability", "")).strip() for item in prompt_matches}
        additional_capabilities = [item for item in capability_matches if item not in prompt_capabilities]
        if additional_capabilities:
            label = (
                f"[commands] additional matching capabilities for '{query}':"
                if prompt_matches
                else f"[commands] matching capabilities for '{query}':"
            )
            self._print_system(label)
            for capability in additional_capabilities:
                details = specs.get(capability, {})
                usage_hint = self._capability_usage_hint(capability, details)
                self._print_system(f"- {usage_hint}")

    @staticmethod
    def _model_timeout_sec() -> float:
        try:
            return max(1.0, float(_env("BRAINDRIVE_MODEL_TIMEOUT_SEC", default="30")))
        except (TypeError, ValueError):
            return 30.0

    @staticmethod
    def _content_to_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            out: List[str] = []
            for item in content:
                if isinstance(item, str):
                    out.append(item)
                    continue
                if isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str):
                        out.append(text)
                        continue
                    nested = item.get("content")
                    if isinstance(nested, str):
                        out.append(nested)
            return "".join(out)
        return ""

    @classmethod
    def _extract_stream_chunk_text(cls, event: Dict[str, Any]) -> str:
        choices = event.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0] if isinstance(choices[0], dict) else {}
            delta = first.get("delta") if isinstance(first.get("delta"), dict) else {}
            text = cls._content_to_text(delta.get("content"))
            if text:
                return text
            message = first.get("message") if isinstance(first.get("message"), dict) else {}
            text = cls._content_to_text(message.get("content"))
            if text:
                return text

        message = event.get("message")
        if isinstance(message, dict):
            text = cls._content_to_text(message.get("content"))
            if text:
                return text

        response = event.get("response")
        if isinstance(response, str):
            return response
        return ""

    def _resolve_stream_target(self, llm_extension: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
        from braindrive_runtime.config import ConfigResolver

        resolver = ConfigResolver(env=os.environ)
        llm = llm_extension if isinstance(llm_extension, dict) else None
        selection = resolver.select_llm(llm)
        requirement = resolver.validate_provider_requirements(selection)
        if requirement:
            raise RuntimeError(requirement)

        defaults = resolver.provider_defaults(selection.provider)
        base_url = defaults.base_url.strip().rstrip("/")
        if not base_url:
            raise RuntimeError(f"Base URL is required for provider {selection.provider}")

        return {
            "provider": selection.provider,
            "model": selection.model,
            "base_url": base_url,
        }

    def _stream_headers_for_provider(self, provider: str) -> Dict[str, str]:
        headers: Dict[str, str] = {
            "Accept": "text/event-stream, application/json",
            "Content-Type": "application/json",
        }

        if provider == "openrouter":
            api_key = _env("BRAINDRIVE_OPENROUTER_API_KEY", default="").strip()
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            site_url = _env("BRAINDRIVE_OPENROUTER_SITE_URL", default="").strip()
            if site_url:
                headers["HTTP-Referer"] = site_url
            app_name = _env("BRAINDRIVE_OPENROUTER_APP_NAME", default="BrainDrive-MVP").strip()
            if app_name:
                headers["X-Title"] = app_name
            return headers

        if provider == "ollama":
            api_key = _env("BRAINDRIVE_OLLAMA_API_KEY", default="").strip()
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            return headers

        raise RuntimeError(f"Unsupported provider for streaming: {provider}")

    @staticmethod
    def _build_stream_request_body(
        *,
        model: str,
        prompt: str,
        messages: Optional[List[Dict[str, str]]] = None,
        llm_extension: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        llm = llm_extension if isinstance(llm_extension, dict) else {}
        stream_messages: List[Dict[str, str]] = []
        if isinstance(messages, list):
            for item in messages:
                if not isinstance(item, dict):
                    continue
                role = str(item.get("role", "")).strip().lower()
                content = str(item.get("content", "")).strip()
                if role not in {"system", "user", "assistant"} or not content:
                    continue
                stream_messages.append({"role": role, "content": content})
        if not stream_messages:
            stream_messages = [{"role": "user", "content": prompt}]

        body: Dict[str, Any] = {
            "model": model,
            "messages": stream_messages,
            "stream": True,
        }

        if isinstance(llm.get("max_tokens"), int) and int(llm["max_tokens"]) > 0:
            body["max_tokens"] = int(llm["max_tokens"])
        if isinstance(llm.get("temperature"), (int, float)):
            body["temperature"] = float(llm["temperature"])
        if isinstance(llm.get("top_p"), (int, float)):
            body["top_p"] = float(llm["top_p"])
        if isinstance(llm.get("stop"), str) and llm["stop"].strip():
            body["stop"] = llm["stop"].strip()
        if isinstance(llm.get("stop"), list):
            stops = [str(item).strip() for item in llm["stop"] if str(item).strip()]
            if stops:
                body["stop"] = stops
        return body

    def _iter_model_stream_chunks(
        self,
        *,
        url: str,
        headers: Dict[str, str],
        body: Dict[str, Any],
        timeout_sec: float,
    ) -> Iterator[str]:
        data = json.dumps(body, ensure_ascii=True).encode("utf-8")
        req = request.Request(url=url, data=data, headers=headers, method="POST")

        try:
            with request.urlopen(req, timeout=timeout_sec) as resp:
                for raw_line in resp:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    if line.startswith(":") or line.startswith("event:"):
                        continue

                    payload_raw = line[5:].strip() if line.startswith("data:") else line
                    if not payload_raw:
                        continue
                    if payload_raw == "[DONE]":
                        break

                    try:
                        event = json.loads(payload_raw)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(event, dict):
                        continue

                    upstream_error = event.get("error")
                    if isinstance(upstream_error, dict):
                        message = str(upstream_error.get("message", "streaming request failed")).strip()
                        raise RuntimeError(message or "streaming request failed")

                    text = self._extract_stream_chunk_text(event)
                    if text:
                        yield text
        except error.HTTPError as exc:
            excerpt = ""
            try:
                excerpt = exc.read().decode("utf-8", errors="replace")[:320]
            except Exception:
                excerpt = ""
            detail = excerpt or f"HTTP {exc.code}"
            raise RuntimeError(detail) from exc
        except error.URLError as exc:
            raise RuntimeError(f"streaming request failed: {exc.reason}") from exc
        except (TimeoutError, OSError) as exc:
            raise RuntimeError(f"streaming request failed: {exc}") from exc

    def _start_ai_stream_line(self, prefix: str) -> None:
        if self.use_color:
            sys.stdout.write(ANSI_AI)
        sys.stdout.write(prefix)
        sys.stdout.flush()

    @staticmethod
    def _write_ai_stream_chunk(chunk: str) -> None:
        sys.stdout.write(chunk)
        sys.stdout.flush()

    def _end_ai_stream_line(self) -> None:
        if self.use_color:
            sys.stdout.write(ANSI_RESET)
        sys.stdout.write("\n")
        sys.stdout.flush()

    def _stream_model_chat_response(
        self,
        *,
        prompt: str,
        messages: Optional[List[Dict[str, str]]] = None,
        llm_extension: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        target = self._resolve_stream_target(llm_extension=llm_extension)
        provider = target["provider"]
        model = target["model"]
        base_url = target["base_url"]
        url = f"{base_url}/chat/completions"
        headers = self._stream_headers_for_provider(provider)
        body = self._build_stream_request_body(model=model, prompt=prompt, messages=messages, llm_extension=llm_extension)

        started_at = time.perf_counter()
        first_chunk_at: Optional[float] = None
        chunk_count = 0
        total_chars = 0
        started = False
        chunks: List[str] = []
        try:
            for chunk in self._iter_model_stream_chunks(
                url=url,
                headers=headers,
                body=body,
                timeout_sec=self._model_timeout_sec(),
            ):
                if not started:
                    self._start_ai_stream_line(f"[{provider}/{model}] ")
                    first_chunk_at = time.perf_counter()
                    started = True
                self._write_ai_stream_chunk(chunk)
                chunks.append(chunk)
                chunk_count += 1
                total_chars += len(chunk)
        except Exception as exc:
            if started:
                self._end_ai_stream_line()
                self._print_system(f"[error] streaming interrupted: {exc}")
                if self.stream_diagnostics and first_chunk_at is not None:
                    elapsed = time.perf_counter() - started_at
                    ttft = first_chunk_at - started_at
                    self._print_system(
                        f"[stream] provider={provider} model={model} ttft={ttft:.3f}s chunks={chunk_count} chars={total_chars} total={elapsed:.3f}s partial=true"
                    )
                return {"handled": True, "text": "".join(chunks), "complete": False}
            return {"handled": False, "text": "", "complete": False}

        if not started:
            return {"handled": False, "text": "", "complete": False}
        self._end_ai_stream_line()
        if self.stream_diagnostics and first_chunk_at is not None:
            elapsed = time.perf_counter() - started_at
            ttft = first_chunk_at - started_at
            avg_chunk = (float(total_chars) / float(chunk_count)) if chunk_count > 0 else 0.0
            self._print_system(
                f"[stream] provider={provider} model={model} ttft={ttft:.3f}s chunks={chunk_count} avg_chunk_chars={avg_chunk:.2f} total={elapsed:.3f}s"
            )
        return {"handled": True, "text": "".join(chunks), "complete": True}

    def _analysis_is_streamable_model_chat(self, analysis: Dict[str, Any]) -> bool:
        if not isinstance(analysis, dict):
            return False
        if bool(analysis.get("clarification_required", False)):
            return False

        intent = str(analysis.get("canonical_intent", ""))
        if intent not in {"model.chat.complete", "model.chat.stream"}:
            return False

        if not self.stream_fallback_only:
            return True
        if intent == "model.chat.stream":
            return True

        reasons = analysis.get("reason_codes", [])
        if not isinstance(reasons, list):
            return False
        return "fallback_model_chat" in [str(value) for value in reasons]

    def _try_stream_model_chat(self, text: str, context: Dict[str, Any]) -> bool:
        if not self.stream_model_chat:
            return False
        if self.raw_output:
            return False

        self._ensure_conversation_id()
        stream_context: Dict[str, Any] = dict(context)
        try:
            max_turns = max(1, int(_env("GATEWAY_PROVIDER_CONTEXT_MAX_TURNS", default="12")))
        except (TypeError, ValueError):
            max_turns = 12
        try:
            max_chars = max(1, int(_env("GATEWAY_PROVIDER_CONTEXT_MAX_CHARS", default="12000")))
        except (TypeError, ValueError):
            max_chars = 12000
        history = self._load_provider_history_messages(self.conversation_id, max_turns=max_turns, max_chars=max_chars)
        if history:
            stream_context["provider_history_messages"] = history

        try:
            analysis = self.analyze_text(text, context=stream_context)
        except Exception:
            return False

        if not self._analysis_is_streamable_model_chat(analysis):
            return False

        payload = analysis.get("payload", {})
        if not isinstance(payload, dict):
            return False
        prompt = str(payload.get("prompt", "")).strip()
        if not prompt:
            return False
        raw_messages = payload.get("messages", [])
        messages = raw_messages if isinstance(raw_messages, list) else None

        stream_result = self._stream_model_chat_response(prompt=prompt, messages=messages)
        if bool(stream_result.get("handled")):
            streamed_text = str(stream_result.get("text", ""))
            if streamed_text:
                try:
                    self._append_stream_chat_record(
                        input_text=text,
                        output_text=streamed_text,
                        complete=bool(stream_result.get("complete", False)),
                    )
                except Exception as exc:
                    # Keep streaming UX non-fatal even if chat persistence fails.
                    self._print_system(
                        f"[warn] chat log persistence failed for {self.conversation_id}: {exc}"
                    )
            return True
        return False

    def print_route_result(self, result: Dict[str, Any]) -> None:
        self._track_active_folder(result)
        if self.raw_output:
            print(json.dumps(result, indent=2, ensure_ascii=True))
            return

        status = str(result.get("status", ""))
        analysis = result.get("analysis", {}) if isinstance(result.get("analysis"), dict) else {}

        if status == "needs_clarification":
            prompt = analysis.get("clarification_prompt") or "Clarification required."
            self._print_ai(f"[clarify] {prompt}")
            return

        response = result.get("route_response", {}) if isinstance(result.get("route_response"), dict) else {}
        intent = str(response.get("intent", ""))

        if not intent:
            self._print_system("[error] route returned no response")
            return

        if intent == "error":
            err = response.get("payload", {}).get("error", {}) if isinstance(response.get("payload"), dict) else {}
            code = err.get("code", "E_UNKNOWN")
            message = err.get("message", "unknown error")
            self._print_system(f"[error] {code}: {message}")
            return

        payload = response.get("payload", {}) if isinstance(response.get("payload"), dict) else {}

        if intent == "chat.response":
            text = payload.get("text", "")
            self._print_ai(str(text))
            if isinstance(payload.get("next_steps"), list):
                for step in payload["next_steps"]:
                    self._print_ai(f"- {step}")
            return

        if intent == "workflow.interview.question":
            self._print_ai(f"[interview] {payload.get('question', '')}")
            return

        if intent == "workflow.interview.ready":
            self._print_ai("[interview] Ready to complete. Type: complete interview")
            return

        if intent == "workflow.interview.completed":
            self._print_ai("[interview] Completed.")
            return

        if intent in {"workflow.spec.generated", "workflow.plan.generated"}:
            key = "spec_markdown" if intent == "workflow.spec.generated" else "plan_markdown"
            text = str(payload.get(key, ""))
            preview = "\n".join(text.splitlines()[:16])
            self._print_ai(preview)
            if len(text.splitlines()) > 16:
                self._print_ai("... (truncated)")
            return

        if intent == "folder.created":
            self._print_ai(f"[folder] created: {payload.get('folder', '')}")
            return

        if intent == "folder.switched":
            self._print_ai(f"[folder] active: {payload.get('active_folder', '')}")
            return

        if intent == "folder.listed":
            folders = payload.get("folders", [])
            active = payload.get("active_folder", "")
            self._print_ai(f"[folder] active={active}")
            if isinstance(folders, list):
                for folder in folders:
                    self._print_ai(f"- {folder}")
            return

        if intent == "model.chat.completed":
            provider = payload.get("provider", "")
            model = payload.get("model", "")
            text = payload.get("text", "")
            self._print_ai(f"[{provider}/{model}] {text}")
            return

        if intent == "model.catalog":
            self._print_ai(f"[model] provider: {payload.get('provider', '')}")
            for model in payload.get("models", []):
                self._print_ai(f"- {model}")
            return

        if intent == "memory.listed":
            entries = payload.get("entries", [])
            if not isinstance(entries, list) or not entries:
                self._print_ai("[files] no entries")
                return
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                path = str(entry.get("path", ""))
                if self.active_folder:
                    prefix = f"{self.active_folder}/"
                    if path.startswith(prefix):
                        path = path[len(prefix) :]
                is_dir = bool(entry.get("is_dir", False))
                suffix = "/" if is_dir else ""
                self._print_ai(f"- {path}{suffix}")
            return

        if intent == "memory.read.result":
            path = str(payload.get("path", ""))
            content = str(payload.get("content", ""))
            self._print_ai(f"[file] {path}")
            self._print_ai(content.rstrip("\n"))
            return

        if intent == "memory.search.results":
            query = str(payload.get("query", ""))
            matches = payload.get("matches", [])
            self._print_ai(f"[search] query={query}")
            if not isinstance(matches, list) or not matches:
                self._print_ai("- no matches")
                return
            for match in matches:
                if not isinstance(match, dict):
                    continue
                path = str(match.get("path", ""))
                preview = str(match.get("preview", "")).strip()
                self._print_ai(f"- {path}: {preview}")
            return

        if intent == "memory.write.applied":
            self._print_ai(f"[file] wrote: {payload.get('path', '')}")
            return

        if intent == "memory.edit.applied":
            self._print_ai(f"[file] updated: {payload.get('path', '')}")
            return

        if intent == "memory.delete.applied":
            self._print_ai(f"[file] deleted: {payload.get('path', '')}")
            return

        if intent == "approval.request":
            changes = payload.get("changes", [])
            self._print_system("[approval] proposal received")
            if isinstance(changes, list):
                for change in changes:
                    if isinstance(change, dict):
                        self._print_system(
                            f"- {change.get('operation', 'change')} {change.get('path', '')}: {change.get('summary', '')}"
                        )
            return

        if intent == "git.committed":
            self._print_system(f"[git] committed: {payload.get('commit', '')}")
            return

        self._print_ai(f"[{intent}] {json.dumps(payload, ensure_ascii=True)}")

    def perform_bootstrap(self) -> None:
        self._print_system("[startup] bootstrapping runtime...")
        bootstrap = self._route_bdp_with_retry(
            "system.bootstrap",
            {},
            retry_codes={"E_NO_ROUTE", "E_NODE_UNAVAILABLE"},
            attempts=30,
            delay_sec=0.5,
        )
        if bootstrap.get("intent") == "error":
            self.print_route_result({"status": "route_error", "route_response": bootstrap})
            raise RuntimeError("bootstrap failed")

        git_ready = self._route_bdp_with_retry(
            "git.init_if_needed",
            {},
            retry_codes={"E_NO_ROUTE", "E_NODE_UNAVAILABLE"},
            attempts=20,
            delay_sec=0.4,
        )
        if git_ready.get("intent") == "error":
            self.print_route_result({"status": "route_error", "route_response": git_ready})
            raise RuntimeError("git init failed")

        self.refresh_active_folder()
        self._print_system("[startup] ready")
        self._print_system(f"[conversation id] {self._ensure_conversation_id()}")

    def run_approval_flow(self, approval_payload: Dict[str, Any]) -> None:
        if not isinstance(approval_payload, dict):
            self._print_system("[error] invalid approval payload")
            return

        changes = approval_payload.get("changes", [])
        if isinstance(changes, list):
            self._print_system("Proposed changes:")
            for change in changes:
                if not isinstance(change, dict):
                    continue
                self._print_system(f"- {change.get('operation', 'change')} {change.get('path', '')}: {change.get('summary', '')}")

        approve = _prompt_yes_no("Approve these changes? [y/N]: ")

        requested = self.route_bdp("approval.request", approval_payload)
        if requested.get("intent") == "error":
            self.print_route_result({"status": "route_error", "route_response": requested})
            return

        request_id = str(requested.get("payload", {}).get("request_id", ""))
        if not request_id:
            self._print_system("[error] missing approval request_id")
            return

        decision = "approved" if approve else "denied"
        resolved = self.route_bdp(
            "approval.resolve",
            {
                "request_id": request_id,
                "decision": decision,
                "decided_by": "owner",
            },
        )
        if resolved.get("intent") == "error":
            self.print_route_result({"status": "route_error", "route_response": resolved})
            return

        if not approve:
            self._print_system("[approval] denied; no changes applied")
            return

        proposed_write = approval_payload.get("proposed_write", {})
        if not isinstance(proposed_write, dict):
            self._print_system("[approval] approved, no write payload")
            return

        path = str(proposed_write.get("path", "")).strip()
        content = proposed_write.get("content")
        if not path or not isinstance(content, str):
            self._print_system("[approval] approved, but no valid write payload")
            return

        write_resp = self.route_bdp(
            "memory.write.propose",
            {"path": path, "content": content},
            extensions={
                "confirmation": {
                    "required": True,
                    "status": "approved",
                    "request_id": request_id,
                }
            },
        )
        if write_resp.get("intent") == "error":
            self.print_route_result({"status": "route_error", "route_response": write_resp})
            return

        scope = Path(path).parent.name or "library"
        commit_resp = self.route_bdp(
            "git.commit.approved_change",
            {
                "paths": [path],
                "reason": "approved_change",
                "source_intent": str(approval_payload.get("intent_being_guarded", "unknown")),
                "approval_request_id": request_id,
                "commit_message": f"feat({scope}): approved change",
            },
        )
        if commit_resp.get("intent") == "error":
            self.print_route_result({"status": "route_error", "route_response": commit_resp})
            return

        self._print_system(f"[approval] applied and committed: {path}")

    def process_text(self, text: str, *, force_confirm: bool = False) -> None:
        context = {
            "active_folder": self.active_folder,
        }
        if not force_confirm:
            try:
                if self._try_stream_model_chat(text, context):
                    return
            except Exception as exc:
                self._print_system(f"[warn] stream unavailable, using routed response: {exc}")

        result = self.route_text(text, confirm=force_confirm, context=context)
        self.print_route_result(result)

        response = result.get("route_response", {}) if isinstance(result.get("route_response"), dict) else {}
        if response.get("intent") == "approval.request":
            payload = response.get("payload", {}) if isinstance(response.get("payload"), dict) else {}
            self.run_approval_flow(payload)
            return

        if response.get("intent") == "error":
            err = response.get("payload", {}).get("error", {}) if isinstance(response.get("payload"), dict) else {}
            if err.get("code") == "E_CONFIRMATION_REQUIRED":
                if _prompt_yes_no("This action requires confirmation. Approve and retry? [y/N]: "):
                    retry = self.route_text(text, confirm=True, context=context)
                    self.print_route_result(retry)


def _prompt_yes_no(prompt: str) -> bool:
    try:
        answer = input(prompt).strip().lower()
    except EOFError:
        return False
    return answer in {"y", "yes"}


def _print_help(use_color: bool = False) -> None:
    lines = [
        "Commands:",
        "  /help            Show this help",
        "  /health          Check router/intent health",
        "  /commands <word> Search prompt examples by keyword",
        "  /prompts         List prompt sections",
        "  /prompts <name>  Show section prompts (for example: /prompts workflow)",
        "  /prompts all     Show all prompts (paged)",
        "  /prompts next    Continue paged prompt output",
        "  /clear           Clear screen and replay startup view",
        "  /raw on|off      Toggle raw JSON output",
        "  /exit            Exit CLI",
        "",
        "Type any normal sentence to interact with BrainDrive.",
    ]
    for line in lines:
        print(_colorize(line, ANSI_SYSTEM, use_color))


def _print_startup_help_hint(use_color: bool = False) -> None:
    lines = [
        "Commands:",
        "  /help            Show available commands",
        "",
        "Type any normal sentence to interact with BrainDrive.",
    ]
    for line in lines:
        print(_colorize(line, ANSI_SYSTEM, use_color))


def _health(client: CliClient) -> None:
    gateway = _request("GET", f"{client.gateway_base}/health", timeout_sec=client.timeout_sec)
    router = _request("GET", f"{client.router_base}/health", timeout_sec=client.timeout_sec)
    intent = _request("GET", f"{client.intent_base}/health", timeout_sec=client.timeout_sec)
    print(_colorize(f"gateway: {json.dumps(gateway, ensure_ascii=True)}", ANSI_SYSTEM, client.use_color))
    print(_colorize(f"router: {json.dumps(router, ensure_ascii=True)}", ANSI_SYSTEM, client.use_color))
    print(_colorize(f"intent: {json.dumps(intent, ensure_ascii=True)}", ANSI_SYSTEM, client.use_color))


def _replay_startup_view(client: CliClient, *, include_bootstrap: bool) -> bool:
    _print_banner(client.use_color)
    try:
        _wait_for_health(client.gateway_base, client.timeout_sec, "gateway.api")
        _wait_for_health(client.router_base, client.timeout_sec, "router.core")
        _wait_for_health(client.intent_base, client.timeout_sec, "intent.router.natural-language")
    except Exception as exc:
        print(_colorize(f"[error] {exc}", ANSI_SYSTEM, client.use_color))
        print(_colorize("Start services with: docker compose up -d", ANSI_SYSTEM, client.use_color))
        return False

    if include_bootstrap:
        try:
            client.perform_bootstrap()
        except Exception as exc:
            print(_colorize(f"[error] startup failed: {exc}", ANSI_SYSTEM, client.use_color))
            return False

    _print_startup_help_hint(client.use_color)
    return True


def _run_repl(client: CliClient, *, include_bootstrap: bool) -> None:
    _print_startup_help_hint(client.use_color)
    while True:
        try:
            line = input(_readline_safe_prompt(client.prompt())).strip()
        except EOFError:
            print("")
            return
        except KeyboardInterrupt:
            print("")
            return

        if not line:
            continue

        if line in {"/exit", "/quit"}:
            return

        if line == "/help":
            _print_help(client.use_color)
            continue

        if line == "/health":
            try:
                _health(client)
            except Exception as exc:
                print(_colorize(f"[error] {exc}", ANSI_SYSTEM, client.use_color))
            continue

        if line == "/commands" or line.startswith("/commands ") or line.startswith("/command "):
            try:
                if line.startswith("/commands "):
                    arg = line[len("/commands") :].strip()
                elif line.startswith("/command "):
                    arg = line[len("/command") :].strip()
                else:
                    arg = ""
                client.handle_commands_search(arg)
            except Exception as exc:
                print(_colorize(f"[error] {exc}", ANSI_SYSTEM, client.use_color))
            continue

        if line == "/clear":
            _clear_screen()
            if not _replay_startup_view(client, include_bootstrap=include_bootstrap):
                return
            continue

        if line == "/prompts" or line.startswith("/prompts "):
            try:
                arg = line[len("/prompts") :].strip()
                client.handle_prompts_command(arg)
            except Exception as exc:
                print(_colorize(f"[error] {exc}", ANSI_SYSTEM, client.use_color))
            continue

        if line.startswith("/raw "):
            value = line.split(None, 1)[1].strip().lower()
            if value in {"on", "true", "1"}:
                client.raw_output = True
                print(_colorize("[cli] raw output enabled", ANSI_SYSTEM, client.use_color))
            elif value in {"off", "false", "0"}:
                client.raw_output = False
                print(_colorize("[cli] raw output disabled", ANSI_SYSTEM, client.use_color))
            else:
                print(_colorize("[error] use /raw on|off", ANSI_SYSTEM, client.use_color))
            continue

        try:
            client.process_text(line)
        except Exception as exc:
            print(_colorize(f"[error] {exc}", ANSI_SYSTEM, client.use_color))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="BrainDrive-MVP terminal CLI")
    parser.add_argument("--router-base", default=DEFAULT_ROUTER_BASE)
    parser.add_argument("--intent-base", default=DEFAULT_INTENT_BASE)
    parser.add_argument("--gateway-base", default=DEFAULT_GATEWAY_BASE)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SEC)
    parser.add_argument("--skip-bootstrap", action="store_true")
    parser.add_argument("--raw", action="store_true")
    parser.add_argument("--message", help="Send one message and exit")
    parser.add_argument("--confirm", action="store_true", help="Send --message with confirmation")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    _setup_line_editing()
    use_color = _should_use_color()

    if not args.message:
        _print_banner(use_color)

    client = CliClient(
        router_base=args.router_base,
        intent_base=args.intent_base,
        gateway_base=args.gateway_base,
        timeout_sec=args.timeout,
        raw_output=bool(args.raw),
    )
    if client.allow_intent_fallback:
        print(
            _colorize(
                "[warn] BRAINDRIVE_CLI_ALLOW_INTENT_FALLBACK=true enables legacy /intent/route fallback.",
                ANSI_SYSTEM,
                client.use_color,
            )
        )

    try:
        _wait_for_health(client.gateway_base, client.timeout_sec, "gateway.api")
        _wait_for_health(client.router_base, client.timeout_sec, "router.core")
        _wait_for_health(client.intent_base, client.timeout_sec, "intent.router.natural-language")
    except Exception as exc:
        print(_colorize(f"[error] {exc}", ANSI_SYSTEM, client.use_color))
        print(_colorize("Start services with: docker compose up -d", ANSI_SYSTEM, client.use_color))
        sys.exit(1)

    if not args.skip_bootstrap:
        try:
            client.perform_bootstrap()
        except Exception as exc:
            print(_colorize(f"[error] startup failed: {exc}", ANSI_SYSTEM, client.use_color))
            sys.exit(1)

    if args.message:
        text = args.message.strip()
        if text == "/commands" or text.startswith("/commands ") or text.startswith("/command "):
            if text.startswith("/commands "):
                arg = text[len("/commands") :].strip()
            elif text.startswith("/command "):
                arg = text[len("/command") :].strip()
            else:
                arg = ""
            client.handle_commands_search(arg)
            return
        if text == "/prompts" or text.startswith("/prompts "):
            arg = text[len("/prompts") :].strip()
            client.handle_prompts_command(arg)
            return
        client.process_text(args.message, force_confirm=bool(args.confirm))
        return

    _run_repl(client, include_bootstrap=not args.skip_bootstrap)


if __name__ == "__main__":
    main()
