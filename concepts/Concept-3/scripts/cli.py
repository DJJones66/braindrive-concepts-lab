#!/usr/bin/env python3
from __future__ import annotations

import argparse
import atexit
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib import error, request

DEFAULT_ROUTER_BASE = os.getenv("CONCEPT3_ROUTER_BASE", "http://localhost:9380")
DEFAULT_INTENT_BASE = os.getenv("CONCEPT3_INTENT_BASE", "http://localhost:9381")
DEFAULT_TIMEOUT_SEC = float(os.getenv("CONCEPT3_CLI_TIMEOUT_SEC", "8.0"))
DEFAULT_HISTORY_FILE = (Path(__file__).resolve().parent.parent / "data" / "runtime" / "state" / ".cli_history").as_posix()
DEFAULT_HISTORY_MAX = int(os.getenv("CONCEPT3_CLI_HISTORY_MAX", "2000"))
DEFAULT_PROMPTS_PAGE_SIZE = int(os.getenv("CONCEPT3_PROMPTS_PAGE_SIZE", "14"))
ANSI_BLUE = "\033[34m"
ANSI_GREEN = "\033[32m"
ANSI_RESET = "\033[0m"


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
    for _ in range(attempts):
        try:
            body = _request("GET", f"{base_url}/health", timeout_sec=timeout_sec)
            if body.get("ok"):
                print(f"[ok] {label} healthy")
                return
        except Exception:
            pass
        time.sleep(0.5)
    raise RuntimeError(f"timed out waiting for {label} health endpoint")


def _setup_line_editing() -> None:
    try:
        import readline
    except Exception:
        return

    history_file = Path(os.getenv("CONCEPT3_CLI_HISTORY_FILE", DEFAULT_HISTORY_FILE)).expanduser()
    try:
        history_file.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        return

    commands = [
        "/help",
        "/health",
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

    def _persist_history() -> None:
        try:
            readline.write_history_file(str(history_file))
        except Exception:
            return

    atexit.register(_persist_history)


def _should_use_color() -> bool:
    mode = os.getenv("CONCEPT3_CLI_COLOR", "auto").strip().lower()
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


def _print_banner(use_color: bool) -> None:
    lines = [
        " ____             _       ____       _           ",
        "| __ ) _ __ __ _(_)_ __ |  _ \\ _ __(_)_   _____ ",
        "|  _ \\| '__/ _` | | '_ \\| | | | '__| \\ \\ / / _ \\",
        "| |_) | | | (_| | | | | | |_| | |  | |\\ V /  __/",
        "|____/|_|  \\__,_|_|_| |_|____/|_|  |_| \\_/ \\___|",
    ]
    if use_color:
        for line in lines:
            print(f"{ANSI_BLUE}{line}{ANSI_RESET}")
    else:
        for line in lines:
            print(line)


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
    def __init__(self, router_base: str, intent_base: str, timeout_sec: float, raw_output: bool = False) -> None:
        self.router_base = router_base.rstrip("/")
        self.intent_base = intent_base.rstrip("/")
        self.timeout_sec = timeout_sec
        self.raw_output = raw_output
        self.active_folder = ""
        self.awaiting_interview_answer = False
        self.use_color = _should_use_color()
        self.prompts_page_size = max(1, DEFAULT_PROMPTS_PAGE_SIZE)
        self._prompt_lines: List[str] = []
        self._prompt_cursor = 0
        self._prompt_title = ""

    def prompt(self) -> str:
        app_label = "braindrive"
        arrow = "> "
        if self.use_color:
            app_label = f"{ANSI_BLUE}braindrive{ANSI_RESET}"
            arrow = f"{ANSI_BLUE}>{ANSI_RESET} "
        if self.active_folder:
            folder_label = self.active_folder
            if self.use_color:
                folder_label = f"{ANSI_GREEN}{self.active_folder}{ANSI_RESET}"
            return f"{app_label} [{folder_label}]{arrow}"
        return f"{app_label}{arrow}"

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

    def _track_interview_state(self, result: Dict[str, Any]) -> None:
        response = self._extract_route_response(result)
        if not response:
            return
        intent = str(response.get("intent", ""))
        if intent == "workflow.interview.question":
            self.awaiting_interview_answer = True
            return
        if intent in {"workflow.interview.ready", "workflow.interview.completed", "folder.switched"}:
            self.awaiting_interview_answer = False
            return

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
        payload: Dict[str, Any] = {
            "message": text,
            "confirm": bool(confirm),
        }
        if extensions:
            payload["extensions"] = extensions
        if context:
            payload["context"] = context
        return _request("POST", f"{self.intent_base}/intent/route", timeout_sec=self.timeout_sec, payload=payload)

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

    def _load_prompt_specs(self) -> Dict[str, Dict[str, List[str]]]:
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
                    spec = specs.setdefault(name, {"examples": [], "descriptions": []})

                    description = capability.get("description")
                    if isinstance(description, str) and description.strip():
                        spec["descriptions"].append(description.strip())

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
            for capability in catalog.keys():
                if isinstance(capability, str) and capability.strip():
                    specs[capability] = {"examples": [], "descriptions": []}

        normalized: Dict[str, Dict[str, List[str]]] = {}
        for capability in sorted(specs.keys()):
            details = specs.get(capability, {})
            normalized[capability] = {
                "examples": self._dedupe_strings(
                    [value for value in details.get("examples", []) if isinstance(value, str)]
                ),
                "descriptions": self._dedupe_strings(
                    [value for value in details.get("descriptions", []) if isinstance(value, str)]
                ),
            }
        return normalized

    @staticmethod
    def _group_prompt_specs(specs: Dict[str, Dict[str, List[str]]]) -> Dict[str, List[str]]:
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
            print("[prompts] no active paged output. Use '/prompts' first.")
            return

        total = len(self._prompt_lines)
        size = self.prompts_page_size
        start = self._prompt_cursor
        end = min(start + size, total)
        page = (start // size) + 1
        pages = ((total - 1) // size) + 1

        print(f"[prompts] {self._prompt_title} (page {page}/{pages})")
        for line in self._prompt_lines[start:end]:
            print(line)

        self._prompt_cursor = end
        if end < total:
            remaining = total - end
            print(f"[prompts] {remaining} more lines. Use '/prompts next' to continue.")
        else:
            print("[prompts] end.")

    @staticmethod
    def _render_prompts_section(
        *,
        section: str,
        grouped: Dict[str, List[str]],
        specs: Dict[str, Dict[str, List[str]]],
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
            print("[prompts] no capabilities discovered")
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
            print(f"[prompts] unknown section: {arg}")
            print(f"[prompts] available: {available}")
            print("[prompts] use '/prompts' to list sections")
            return

        lines = self._render_prompts_section(section=arg, grouped=grouped, specs=specs)
        if "model.chat.complete" in specs and arg == "model":
            lines.append("")
            lines.append("fallback:")
            lines.append("- Any other normal sentence will route to model chat.")
        self._start_prompt_pager(f"section '{arg}'", lines)

    def print_route_result(self, result: Dict[str, Any]) -> None:
        self._track_active_folder(result)
        self._track_interview_state(result)
        if self.raw_output:
            print(json.dumps(result, indent=2, ensure_ascii=True))
            return

        status = str(result.get("status", ""))
        analysis = result.get("analysis", {}) if isinstance(result.get("analysis"), dict) else {}

        if status == "needs_clarification":
            prompt = analysis.get("clarification_prompt") or "Clarification required."
            print(f"[clarify] {prompt}")
            return

        response = result.get("route_response", {}) if isinstance(result.get("route_response"), dict) else {}
        intent = str(response.get("intent", ""))

        if not intent:
            print("[error] route returned no response")
            return

        if intent == "error":
            err = response.get("payload", {}).get("error", {}) if isinstance(response.get("payload"), dict) else {}
            code = err.get("code", "E_UNKNOWN")
            message = err.get("message", "unknown error")
            print(f"[error] {code}: {message}")
            return

        payload = response.get("payload", {}) if isinstance(response.get("payload"), dict) else {}

        if intent == "chat.response":
            text = payload.get("text", "")
            print(text)
            if isinstance(payload.get("next_steps"), list):
                for step in payload["next_steps"]:
                    print(f"- {step}")
            return

        if intent == "workflow.interview.question":
            print(f"[interview] {payload.get('question', '')}")
            return

        if intent == "workflow.interview.ready":
            print("[interview] Ready to complete. Type: complete interview")
            return

        if intent == "workflow.interview.completed":
            print("[interview] Completed.")
            return

        if intent in {"workflow.spec.generated", "workflow.plan.generated"}:
            key = "spec_markdown" if intent == "workflow.spec.generated" else "plan_markdown"
            text = str(payload.get(key, ""))
            preview = "\n".join(text.splitlines()[:16])
            print(preview)
            if len(text.splitlines()) > 16:
                print("... (truncated)")
            return

        if intent == "folder.created":
            print(f"[folder] created: {payload.get('folder', '')}")
            return

        if intent == "folder.switched":
            print(f"[folder] active: {payload.get('active_folder', '')}")
            return

        if intent == "folder.listed":
            folders = payload.get("folders", [])
            active = payload.get("active_folder", "")
            print(f"[folder] active={active}")
            if isinstance(folders, list):
                for folder in folders:
                    print(f"- {folder}")
            return

        if intent == "model.chat.completed":
            provider = payload.get("provider", "")
            model = payload.get("model", "")
            text = payload.get("text", "")
            print(f"[{provider}/{model}] {text}")
            return

        if intent == "model.catalog":
            print(f"[model] provider: {payload.get('provider', '')}")
            for model in payload.get("models", []):
                print(f"- {model}")
            return

        if intent == "memory.listed":
            entries = payload.get("entries", [])
            if not isinstance(entries, list) or not entries:
                print("[files] no entries")
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
                print(f"- {path}{suffix}")
            return

        if intent == "memory.read.result":
            path = str(payload.get("path", ""))
            content = str(payload.get("content", ""))
            print(f"[file] {path}")
            print(content.rstrip("\n"))
            return

        if intent == "memory.search.results":
            query = str(payload.get("query", ""))
            matches = payload.get("matches", [])
            print(f"[search] query={query}")
            if not isinstance(matches, list) or not matches:
                print("- no matches")
                return
            for match in matches:
                if not isinstance(match, dict):
                    continue
                path = str(match.get("path", ""))
                preview = str(match.get("preview", "")).strip()
                print(f"- {path}: {preview}")
            return

        if intent == "memory.write.applied":
            print(f"[file] wrote: {payload.get('path', '')}")
            return

        if intent == "memory.edit.applied":
            print(f"[file] updated: {payload.get('path', '')}")
            return

        if intent == "memory.delete.applied":
            print(f"[file] deleted: {payload.get('path', '')}")
            return

        if intent == "approval.request":
            changes = payload.get("changes", [])
            print("[approval] proposal received")
            if isinstance(changes, list):
                for change in changes:
                    if isinstance(change, dict):
                        print(f"- {change.get('operation', 'change')} {change.get('path', '')}: {change.get('summary', '')}")
            return

        if intent == "git.committed":
            print(f"[git] committed: {payload.get('commit', '')}")
            return

        print(f"[{intent}] {json.dumps(payload, ensure_ascii=True)}")

    def perform_bootstrap(self) -> None:
        print("[startup] bootstrapping runtime...")
        bootstrap = self.route_bdp("system.bootstrap", {})
        if bootstrap.get("intent") == "error":
            self.print_route_result({"status": "route_error", "route_response": bootstrap})
            raise RuntimeError("bootstrap failed")

        git_ready = self.route_bdp("git.init_if_needed", {})
        if git_ready.get("intent") == "error":
            self.print_route_result({"status": "route_error", "route_response": git_ready})
            raise RuntimeError("git init failed")

        self.refresh_active_folder()
        print("[startup] ready")

    def run_approval_flow(self, approval_payload: Dict[str, Any]) -> None:
        if not isinstance(approval_payload, dict):
            print("[error] invalid approval payload")
            return

        changes = approval_payload.get("changes", [])
        if isinstance(changes, list):
            print("Proposed changes:")
            for change in changes:
                if not isinstance(change, dict):
                    continue
                print(f"- {change.get('operation', 'change')} {change.get('path', '')}: {change.get('summary', '')}")

        approve = _prompt_yes_no("Approve these changes? [y/N]: ")

        requested = self.route_bdp("approval.request", approval_payload)
        if requested.get("intent") == "error":
            self.print_route_result({"status": "route_error", "route_response": requested})
            return

        request_id = str(requested.get("payload", {}).get("request_id", ""))
        if not request_id:
            print("[error] missing approval request_id")
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
            print("[approval] denied; no changes applied")
            return

        proposed_write = approval_payload.get("proposed_write", {})
        if not isinstance(proposed_write, dict):
            print("[approval] approved, no write payload")
            return

        path = str(proposed_write.get("path", "")).strip()
        content = proposed_write.get("content")
        if not path or not isinstance(content, str):
            print("[approval] approved, but no valid write payload")
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

        print(f"[approval] applied and committed: {path}")

    def process_text(self, text: str, *, force_confirm: bool = False) -> None:
        context = {
            "active_folder": self.active_folder,
            "interview": {"awaiting_answer": self.awaiting_interview_answer},
        }
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


def _print_help() -> None:
    print("Commands:")
    print("  /help            Show this help")
    print("  /health          Check router/intent health")
    print("  /prompts         List prompt sections")
    print("  /prompts <name>  Show section prompts (for example: /prompts workflow)")
    print("  /prompts all     Show all prompts (paged)")
    print("  /prompts next    Continue paged prompt output")
    print("  /clear           Clear screen and replay startup view")
    print("  /raw on|off      Toggle raw JSON output")
    print("  /exit            Exit CLI")
    print("")
    print("Type any normal sentence to interact with BrainDrive.")


def _health(client: CliClient) -> None:
    router = _request("GET", f"{client.router_base}/health", timeout_sec=client.timeout_sec)
    intent = _request("GET", f"{client.intent_base}/health", timeout_sec=client.timeout_sec)
    print(f"router: {json.dumps(router, ensure_ascii=True)}")
    print(f"intent: {json.dumps(intent, ensure_ascii=True)}")


def _replay_startup_view(client: CliClient, *, include_bootstrap: bool) -> bool:
    _print_banner(client.use_color)
    try:
        _wait_for_health(client.router_base, client.timeout_sec, "router.core")
        _wait_for_health(client.intent_base, client.timeout_sec, "intent.router.natural-language")
    except Exception as exc:
        print(f"[error] {exc}")
        print("Start services with: docker compose up -d")
        return False

    if include_bootstrap:
        try:
            client.perform_bootstrap()
        except Exception as exc:
            print(f"[error] startup failed: {exc}")
            return False

    _print_help()
    return True


def _run_repl(client: CliClient, *, include_bootstrap: bool) -> None:
    _print_help()
    while True:
        try:
            line = input(client.prompt()).strip()
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
            _print_help()
            continue

        if line == "/health":
            try:
                _health(client)
            except Exception as exc:
                print(f"[error] {exc}")
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
                print(f"[error] {exc}")
            continue

        if line.startswith("/raw "):
            value = line.split(None, 1)[1].strip().lower()
            if value in {"on", "true", "1"}:
                client.raw_output = True
                print("[cli] raw output enabled")
            elif value in {"off", "false", "0"}:
                client.raw_output = False
                print("[cli] raw output disabled")
            else:
                print("[error] use /raw on|off")
            continue

        try:
            client.process_text(line)
        except Exception as exc:
            print(f"[error] {exc}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="BrainDrive Concept-3 terminal CLI")
    parser.add_argument("--router-base", default=DEFAULT_ROUTER_BASE)
    parser.add_argument("--intent-base", default=DEFAULT_INTENT_BASE)
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
        timeout_sec=args.timeout,
        raw_output=bool(args.raw),
    )

    try:
        _wait_for_health(client.router_base, client.timeout_sec, "router.core")
        _wait_for_health(client.intent_base, client.timeout_sec, "intent.router.natural-language")
    except Exception as exc:
        print(f"[error] {exc}")
        print("Start services with: docker compose up -d")
        sys.exit(1)

    if not args.skip_bootstrap:
        try:
            client.perform_bootstrap()
        except Exception as exc:
            print(f"[error] startup failed: {exc}")
            sys.exit(1)

    if args.message:
        text = args.message.strip()
        if text == "/prompts" or text.startswith("/prompts "):
            arg = text[len("/prompts") :].strip()
            client.handle_prompts_command(arg)
            return
        client.process_text(args.message, force_confirm=bool(args.confirm))
        return

    _run_repl(client, include_bootstrap=not args.skip_bootstrap)


if __name__ == "__main__":
    main()
