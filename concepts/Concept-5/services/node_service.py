#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Type
from urllib.parse import parse_qs, urlsplit

from braindrive_runtime.nodes import (
    ApprovalGateNode,
    AuditLogNode,
    ChatGeneralNode,
    FolderWorkflowNode,
    GitOpsNode,
    MemoryFsNode,
    OllamaModelNode,
    OpenRouterModelNode,
    RuntimeBootstrapNode,
    ScraplingNode,
    SkillWorkflowNode,
    WebConsoleNode,
)
from braindrive_runtime.nodes.base import NodeContext, ProtocolNode
from braindrive_runtime.persistence import Persistence
from braindrive_runtime.protocol import http_post_json, make_error, new_uuid, validate_core
from braindrive_runtime.service_registration import start_registration_loop
from braindrive_runtime.state import WorkflowState

PORT = int(os.getenv("NODE_PORT", "8110"))
NODE_KIND = os.getenv("NODE_KIND", "chat_general").strip().lower()
NODE_ENDPOINT_URL = os.getenv("NODE_ENDPOINT_URL", f"http://localhost:{PORT}/bdp")
REGISTRATION_TOKEN = os.getenv("ROUTER_REGISTRATION_TOKEN", "braindrive-mvp-dev-token")
REGISTER_URL = os.getenv("ROUTER_REGISTER_URL", "http://node-router:8080/router/node/register")
HEARTBEAT_URL = os.getenv("ROUTER_HEARTBEAT_URL", "http://node-router:8080/router/node/heartbeat")
HEARTBEAT_SEC = float(os.getenv("ROUTER_HEARTBEAT_SEC", "5.0"))
REGISTER_RETRY_SEC = float(os.getenv("ROUTER_REGISTER_RETRY_SEC", "2.0"))
ROUTER_DIRECT_ROUTE_URL = os.getenv("ROUTER_DIRECT_ROUTE_URL", "http://node-router:8080/route")
ROUTER_DIRECT_ROUTE_TIMEOUT_SEC = float(os.getenv("ROUTER_DIRECT_ROUTE_TIMEOUT_SEC", "70.0"))

LIBRARY_ROOT = Path(os.getenv("BRAINDRIVE_LIBRARY_ROOT", "/workspace/data/library"))
RUNTIME_DIR = Path(os.getenv("BRAINDRIVE_RUNTIME_DIR", "/workspace/data/runtime"))

LIBRARY_ROOT.mkdir(parents=True, exist_ok=True)
RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

NODE_MAP: Dict[str, Type[ProtocolNode]] = {
    "runtime_bootstrap": RuntimeBootstrapNode,
    "memory_fs": MemoryFsNode,
    "folder": FolderWorkflowNode,
    "skill": SkillWorkflowNode,
    "approval_gate": ApprovalGateNode,
    "git_ops": GitOpsNode,
    "model_openrouter": OpenRouterModelNode,
    "model_ollama": OllamaModelNode,
    "scrapling": ScraplingNode,
    "web_console": WebConsoleNode,
    "chat_general": ChatGeneralNode,
    "audit_log": AuditLogNode,
}


def build_node() -> ProtocolNode:
    node_cls = NODE_MAP.get(NODE_KIND)
    if node_cls is None:
        raise ValueError(f"Unknown NODE_KIND: {NODE_KIND}")

    persistence = Persistence(RUNTIME_DIR)
    workflow_state = WorkflowState(persistence)

    def _route_message(message: Dict[str, Any]) -> Dict[str, Any]:
        return http_post_json(ROUTER_DIRECT_ROUTE_URL, message, timeout_sec=ROUTER_DIRECT_ROUTE_TIMEOUT_SEC)

    ctx = NodeContext(
        library_root=LIBRARY_ROOT,
        persistence=persistence,
        registration_token=REGISTRATION_TOKEN,
        workflow_state=workflow_state,
        env=dict(os.environ),
        route_message=_route_message,
    )
    return node_cls(ctx)


NODE = build_node()
DESCRIPTOR = NODE.descriptor()
DESCRIPTOR.endpoint_url = NODE_ENDPOINT_URL

start_registration_loop(
    descriptor=DESCRIPTOR.to_dict(),
    register_url=REGISTER_URL,
    heartbeat_url=HEARTBEAT_URL,
    heartbeat_sec=HEARTBEAT_SEC,
    register_retry_sec=REGISTER_RETRY_SEC,
)

WEB_TERMINAL_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>BrainDrive Web Console</title>
  <style>
    :root {
      --bg: #0d1117;
      --panel: #161b22;
      --line: #30363d;
      --text: #c9d1d9;
      --muted: #8b949e;
      --accent: #2f81f7;
      --good: #2ea043;
      --warn: #d29922;
      --bad: #f85149;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font: 14px/1.4 ui-monospace, SFMono-Regular, Menlo, monospace;
    }
    .wrap {
      max-width: 1100px;
      margin: 0 auto;
      padding: 12px;
      display: grid;
      grid-template-columns: 280px 1fr;
      gap: 12px;
      height: 100vh;
    }
    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px;
      overflow: auto;
    }
    .left h2 {
      font-size: 13px;
      margin: 6px 0;
      color: var(--muted);
    }
    label {
      display: block;
      margin: 8px 0 3px;
      color: var(--muted);
      font-size: 12px;
    }
    input, select, button, textarea {
      width: 100%;
      background: #0f141b;
      border: 1px solid var(--line);
      color: var(--text);
      border-radius: 6px;
      padding: 8px;
      font: inherit;
    }
    button {
      cursor: pointer;
      background: #1b2532;
    }
    button:hover { border-color: var(--accent); }
    .row { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
    .status { margin-top: 10px; font-size: 12px; color: var(--muted); }
    #terminal {
      height: calc(100vh - 130px);
      overflow: auto;
      white-space: pre-wrap;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #0f141b;
      padding: 10px;
    }
    #input-row { display: grid; grid-template-columns: 1fr auto auto; gap: 8px; margin-top: 8px; }
    #send-btn { width: 110px; background: #203852; }
    #approve-btn { width: 140px; background: #3d3317; }
    .ok { color: var(--good); }
    .warn { color: var(--warn); }
    .err { color: var(--bad); }
    @media (max-width: 900px) {
      .wrap { grid-template-columns: 1fr; height: auto; min-height: 100vh; }
      #terminal { height: 52vh; }
    }
  </style>
</head>
<body>
  <div class="wrap">
    <aside class="panel left">
      <h2>Session</h2>
      <label>Actor ID</label>
      <input id="actor-id" value="web.user">
      <label>Roles (comma)</label>
      <input id="roles" value="operator">
      <label>Origin</label>
      <input id="origin">
      <label>Target</label>
      <select id="target"></select>
      <div class="row" style="margin-top:8px;">
        <button id="open-btn">Open</button>
        <button id="close-btn">Close</button>
      </div>
      <div class="status" id="session-status">session: closed</div>
      <h2 style="margin-top:18px;">Quick Commands</h2>
      <button class="quick" data-cmd="/help">/help</button>
      <button class="quick" data-cmd="/health">/health</button>
      <button class="quick" data-cmd="/prompts">/prompts</button>
      <button class="quick" data-cmd="/targets">/targets</button>
      <button class="quick" data-cmd="/raw on">/raw on</button>
      <button class="quick" data-cmd="/raw off">/raw off</button>
    </aside>
    <main class="panel">
      <div id="terminal"></div>
      <div id="input-row">
        <input id="cmd-input" placeholder="Type natural language or slash command...">
        <button id="send-btn">Send</button>
        <button id="approve-btn" title="Approve pending command retry">Approve Retry</button>
      </div>
    </main>
  </div>
  <script>
    const $ = (id) => document.getElementById(id);
    const state = { sessionId: "", pendingRequestId: "", lastCommand: "" };

    function actorPayload() {
      const actor = $("actor-id").value.trim() || "web.user";
      const roles = $("roles").value.split(",").map(v => v.trim()).filter(Boolean);
      return { actor_id: actor, roles: roles.length ? roles : ["operator"] };
    }

    function write(line, cls="") {
      const div = document.createElement("div");
      if (cls) div.className = cls;
      div.textContent = line;
      $("terminal").appendChild(div);
      $("terminal").scrollTop = $("terminal").scrollHeight;
    }

    async function post(path, body) {
      const res = await fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      return await res.json();
    }

    async function get(path) {
      const res = await fetch(path);
      return await res.json();
    }

    function renderResponse(data) {
      if (data.intent === "error") {
        const err = (data.payload || {}).error || {};
        write(`[${err.code || "E_NODE_ERROR"}] ${err.message || "error"}`, "err");
        return;
      }
      if (data.intent === "web.console.session.approval_required") {
        state.pendingRequestId = data.payload.approval_request_id || "";
        write(`Approval required: ${state.pendingRequestId}`, "warn");
        return;
      }
      if (data.intent === "web.console.session.events") {
        const events = (data.payload || {}).events || [];
        for (const evt of events) {
          const p = evt.payload || {};
          if (typeof p.data === "string" && p.data.length) {
            write(p.data);
          }
        }
        return;
      }
      write(JSON.stringify(data, null, 2));
    }

    async function loadTargets() {
      const actor = actorPayload();
      const q = encodeURIComponent(actor.actor_id);
      const data = await get(`/webterm/targets?actor_id=${q}&roles=${encodeURIComponent(actor.roles.join(","))}`);
      if (data.intent === "web.console.targets") {
        $("target").innerHTML = "";
        for (const t of data.payload.targets || []) {
          const o = document.createElement("option");
          o.value = t;
          o.textContent = t;
          $("target").appendChild(o);
        }
        const def = data.payload.default_target || "";
        if (def) $("target").value = def;
      } else {
        renderResponse(data);
      }
    }

    async function openSession() {
      const actor = actorPayload();
      const data = await post("/webterm/session/open", {
        ...actor,
        origin: $("origin").value.trim() || window.location.origin,
        target: $("target").value,
      });
      if (data.intent === "web.console.session.ready") {
        state.sessionId = data.payload.session_id || "";
        $("session-status").textContent = `session: ${state.sessionId}`;
        write(data.payload.banner || "session ready", "ok");
      } else {
        renderResponse(data);
      }
    }

    async function closeSession() {
      if (!state.sessionId) return;
      const actor = actorPayload();
      const data = await post("/webterm/session/close", {
        ...actor,
        session_id: state.sessionId,
      });
      renderResponse(data);
      state.sessionId = "";
      state.pendingRequestId = "";
      $("session-status").textContent = "session: closed";
    }

    async function sendCommand(confirmRetry=false) {
      if (!state.sessionId) {
        write("Open a session first.", "warn");
        return;
      }
      const cmd = (confirmRetry ? state.lastCommand : $("cmd-input").value).trim();
      if (!cmd) return;
      state.lastCommand = cmd;
      if (!confirmRetry) $("cmd-input").value = "";

      write(`> ${cmd}`, "ok");
      const actor = actorPayload();
      const body = {
        ...actor,
        session_id: state.sessionId,
        text: cmd,
      };
      if (confirmRetry && state.pendingRequestId) {
        body.confirm = true;
        body.approval_request_id = state.pendingRequestId;
      }
      const data = await post("/webterm/message", body);
      if (confirmRetry && data.intent !== "web.console.session.approval_required") {
        state.pendingRequestId = "";
      }
      renderResponse(data);
    }

    window.addEventListener("DOMContentLoaded", async () => {
      $("origin").value = window.location.origin;
      await loadTargets();
      $("open-btn").addEventListener("click", openSession);
      $("close-btn").addEventListener("click", closeSession);
      $("send-btn").addEventListener("click", () => sendCommand(false));
      $("approve-btn").addEventListener("click", () => sendCommand(true));
      $("cmd-input").addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          sendCommand(false);
        }
      });
      for (const btn of document.querySelectorAll(".quick")) {
        btn.addEventListener("click", () => {
          $("cmd-input").value = btn.getAttribute("data-cmd") || "";
          sendCommand(false);
        });
      }
      write("BrainDrive Web Console loaded.");
    });
  </script>
</body>
</html>
"""


class NodeHandler(BaseHTTPRequestHandler):
    server_version = f"{DESCRIPTOR.node_id}/0.1"

    def _send_json(self, code: int, body: Dict[str, Any]) -> None:
        payload = json.dumps(body, ensure_ascii=True).encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError, OSError):
            # Client disconnected before response write completed.
            return

    def _read_json(self) -> Optional[Dict[str, Any]]:
        try:
            size = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(size)
            parsed = json.loads(raw.decode("utf-8"))
        except Exception:
            return None
        return parsed if isinstance(parsed, dict) else None

    def _send_html(self, code: int, html: str) -> None:
        payload = html.encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

    def _query(self) -> Dict[str, str]:
        parsed = urlsplit(self.path)
        values = parse_qs(parsed.query, keep_blank_values=True)
        out: Dict[str, str] = {}
        for key, items in values.items():
            if not items:
                continue
            out[key] = str(items[0])
        return out

    def _path_only(self) -> str:
        return urlsplit(self.path).path

    def _webterm_extensions(self, body: Dict[str, Any], *, allow_confirmation: bool = False) -> Dict[str, Any]:
        actor_id = str(body.get("actor_id", "")).strip() or str(self.headers.get("X-Actor-Id", "")).strip() or "web.user"
        roles_raw = body.get("roles", [])
        if not isinstance(roles_raw, list):
            roles_raw = [item.strip() for item in str(self.headers.get("X-Actor-Roles", "")).split(",") if item.strip()]
        roles = [str(item).strip() for item in roles_raw if str(item).strip()]
        if not roles:
            roles = ["operator"]

        extensions: Dict[str, Any] = {
            "identity": {
                "actor_id": actor_id,
                "roles": roles,
            }
        }

        if allow_confirmation and bool(body.get("confirm", False)):
            request_id = str(body.get("approval_request_id", "")).strip()
            if request_id:
                extensions["confirmation"] = {
                    "required": True,
                    "status": "approved",
                    "request_id": request_id,
                }
        return extensions

    def _webterm_dispatch(self, intent: str, payload: Dict[str, Any], extensions: Dict[str, Any]) -> Dict[str, Any]:
        message = {
            "protocol_version": "0.1",
            "message_id": new_uuid(),
            "intent": intent,
            "payload": payload,
            "extensions": extensions,
        }
        return NODE.handle(message)

    def _handle_webterm_get(self) -> bool:
        if NODE_KIND != "web_console":
            return False

        path = self._path_only()
        query = self._query()
        actor_id = str(query.get("actor_id", "")).strip() or "web.user"
        roles = [item.strip() for item in str(query.get("roles", "operator")).split(",") if item.strip()]
        extensions = {"identity": {"actor_id": actor_id, "roles": roles or ["operator"]}}

        if path == "/ui/terminal":
            self._send_html(200, WEB_TERMINAL_HTML)
            return True
        if path == "/webterm/health":
            self._send_json(
                200,
                {
                    "ok": True,
                    "service": DESCRIPTOR.node_id,
                    "node_kind": NODE_KIND,
                },
            )
            return True
        if path == "/webterm/targets":
            response = self._webterm_dispatch("web.console.targets.list", {}, extensions)
            self._send_json(200, response)
            return True
        if path == "/webterm/guides":
            response = self._webterm_dispatch("web.console.guides.list", {}, extensions)
            self._send_json(200, response)
            return True
        return False

    def _handle_webterm_post(self) -> bool:
        if NODE_KIND != "web_console":
            return False

        path = self._path_only()
        body = self._read_json()
        if body is None:
            self._send_json(400, {"ok": False, "error": "Invalid JSON"})
            return True

        if path == "/webterm/session/open":
            payload = {
                "origin": str(body.get("origin", "")).strip() or str(self.headers.get("Origin", "")).strip(),
                "target": str(body.get("target", "")).strip(),
                "source_ip": str(body.get("source_ip", "")).strip(),
            }
            response = self._webterm_dispatch(
                "web.console.session.open",
                payload,
                self._webterm_extensions(body),
            )
            self._send_json(200, response)
            return True

        if path == "/webterm/session/close":
            payload = {
                "session_id": str(body.get("session_id", "")).strip(),
                "reason": str(body.get("reason", "requested")).strip(),
            }
            response = self._webterm_dispatch(
                "web.console.session.close",
                payload,
                self._webterm_extensions(body),
            )
            self._send_json(200, response)
            return True

        if path == "/webterm/session/event":
            payload = {
                "session_id": str(body.get("session_id", "")).strip(),
                "event": str(body.get("event", "")).strip(),
                "payload": body.get("payload", {}),
            }
            response = self._webterm_dispatch(
                "web.console.session.event",
                payload,
                self._webterm_extensions(body, allow_confirmation=True),
            )
            self._send_json(200, response)
            return True

        if path == "/webterm/message":
            command = str(body.get("text", "")).strip()
            payload = {
                "session_id": str(body.get("session_id", "")).strip(),
                "event": "terminal.input",
                "payload": {"data": command},
            }
            response = self._webterm_dispatch(
                "web.console.session.event",
                payload,
                self._webterm_extensions(body, allow_confirmation=True),
            )
            self._send_json(200, response)
            return True

        return False

    def do_GET(self) -> None:
        if self._handle_webterm_get():
            return

        if self.path == "/health":
            self._send_json(
                200,
                {
                    "ok": True,
                    "service": DESCRIPTOR.node_id,
                    "capabilities": [cap.name for cap in DESCRIPTOR.capabilities],
                },
            )
            return

        if self.path == "/descriptor":
            self._send_json(200, {"ok": True, "descriptor": DESCRIPTOR.to_dict()})
            return

        self._send_json(404, {"ok": False})

    def do_POST(self) -> None:
        if self._handle_webterm_post():
            return

        if self.path != "/bdp":
            self._send_json(404, {"ok": False})
            return

        message = self._read_json()
        if message is None:
            self._send_json(200, make_error("E_BAD_MESSAGE", "Invalid JSON body", None))
            return

        validation_error = validate_core(message)
        if validation_error:
            self._send_json(200, validation_error)
            return

        try:
            response = NODE.handle(message)
        except Exception as exc:
            response = make_error(
                "E_INTERNAL",
                f"{DESCRIPTOR.node_id} exception: {type(exc).__name__}",
                message.get("message_id"),
                details={"error": str(exc)},
            )

        self._send_json(200, response)

    def log_message(self, format: str, *args: Any) -> None:
        return


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", PORT), NodeHandler)
    print(f"{DESCRIPTOR.node_id} ({NODE_KIND}) listening on :{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
