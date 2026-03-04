from __future__ import annotations

WEB_TERMINAL_HTML = """<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\">
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
  <title>BrainDrive Web Console</title>
  <style>
    :root {
      --bg: #0f172a;
      --panel: #111827;
      --line: #334155;
      --text: #e5e7eb;
      --muted: #94a3b8;
      --ok: #22c55e;
      --warn: #f59e0b;
      --err: #ef4444;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: linear-gradient(180deg, #0b1220, #0f172a 35%, #111827);
      color: var(--text);
      font: 14px/1.45 ui-monospace, SFMono-Regular, Menlo, monospace;
    }
    .wrap {
      max-width: 1120px;
      margin: 0 auto;
      min-height: 100vh;
      padding: 12px;
      display: grid;
      grid-template-columns: 300px 1fr;
      gap: 12px;
    }
    .panel {
      background: rgba(17,24,39,0.92);
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 10px;
      overflow: auto;
    }
    h2 {
      margin: 8px 0;
      font-size: 12px;
      color: var(--muted);
      letter-spacing: .06em;
      text-transform: uppercase;
    }
    label {
      display: block;
      margin: 8px 0 4px;
      color: var(--muted);
      font-size: 12px;
    }
    input, select, button {
      width: 100%;
      background: #0b1220;
      border: 1px solid var(--line);
      color: var(--text);
      border-radius: 7px;
      padding: 8px;
      font: inherit;
    }
    button { cursor: pointer; }
    button:hover { border-color: #60a5fa; }
    .row { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 8px; }
    .status { margin-top: 10px; font-size: 12px; color: var(--muted); }
    #terminal {
      height: calc(100vh - 140px);
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #020617;
      padding: 10px;
      overflow: auto;
      white-space: pre-wrap;
    }
    #input-row { display: grid; grid-template-columns: 1fr auto auto; gap: 8px; margin-top: 8px; }
    #send-btn { width: 120px; background: #1e3a8a; }
    #approve-btn { width: 150px; background: #713f12; }
    .ok { color: var(--ok); }
    .warn { color: var(--warn); }
    .err { color: var(--err); }
    @media (max-width: 960px) {
      .wrap { grid-template-columns: 1fr; }
      #terminal { height: 56vh; }
    }
  </style>
</head>
<body>
  <div class=\"wrap\">
    <aside class=\"panel\">
      <h2>Session</h2>
      <label>Actor ID</label>
      <input id=\"actor-id\" value=\"web.user\">
      <label>Roles (comma)</label>
      <input id=\"roles\" value=\"operator\">
      <label>Origin</label>
      <input id=\"origin\">
      <label>Target</label>
      <select id=\"target\"></select>
      <div class=\"row\">
        <button id=\"open-btn\">Open</button>
        <button id=\"close-btn\">Close</button>
      </div>
      <div class=\"status\" id=\"session-status\">session: closed</div>

      <h2 style=\"margin-top:18px;\">Quick</h2>
      <button class=\"quick\" data-cmd=\"/help\">/help</button>
      <button class=\"quick\" data-cmd=\"/health\">/health</button>
      <button class=\"quick\" data-cmd=\"/targets\">/targets</button>
      <button class=\"quick\" data-cmd=\"/raw on\">/raw on</button>
      <button class=\"quick\" data-cmd=\"/raw off\">/raw off</button>
    </aside>

    <main class=\"panel\">
      <div id=\"terminal\"></div>
      <div id=\"input-row\">
        <input id=\"cmd-input\" placeholder=\"Type a command or natural language...\">
        <button id=\"send-btn\">Send</button>
        <button id=\"approve-btn\" title=\"Approve pending command retry\">Approve Retry</button>
      </div>
    </main>
  </div>

  <script>
    const $ = (id) => document.getElementById(id);
    const state = { sessionId: \"\", pendingRequestId: \"\", lastCommand: \"\", opening: false };
    const STORAGE_KEYS = {
      actorId: \"bd.webterm.actor_id\",
      sessionPrefix: \"bd.webterm.session_id:\",
    };

    function randomId8() {
      return Math.random().toString(36).slice(2, 10);
    }

    function actorSessionStorageKey(actorId) {
      return `${STORAGE_KEYS.sessionPrefix}${actorId}`;
    }

    function setSessionState(sessionId, actorId) {
      state.sessionId = sessionId || \"\";
      state.pendingRequestId = \"\";
      $(\"session-status\").textContent = state.sessionId ? `session: ${state.sessionId}` : \"session: closed\";
      if (actorId && state.sessionId) {
        localStorage.setItem(actorSessionStorageKey(actorId), state.sessionId);
      } else if (actorId) {
        localStorage.removeItem(actorSessionStorageKey(actorId));
      }
    }

    function ensureActorIdentity() {
      const current = $(\"actor-id\").value.trim();
      const stored = (localStorage.getItem(STORAGE_KEYS.actorId) || \"\").trim();

      if (stored) {
        $(\"actor-id\").value = stored;
        return stored;
      }

      if (current && current !== \"web.user\") {
        localStorage.setItem(STORAGE_KEYS.actorId, current);
        return current;
      }

      const generated = `web.user.${randomId8()}`;
      localStorage.setItem(STORAGE_KEYS.actorId, generated);
      $(\"actor-id\").value = generated;
      write(`[startup] actor id assigned: ${generated}`);
      return generated;
    }

    function rotateActorIdentity() {
      const generated = `web.user.${randomId8()}`;
      localStorage.setItem(STORAGE_KEYS.actorId, generated);
      $(\"actor-id\").value = generated;
      return generated;
    }

    function actorPayload() {
      const actor = $(\"actor-id\").value.trim() || ensureActorIdentity();
      const roles = $(\"roles\").value.split(\",\").map(v => v.trim()).filter(Boolean);
      return { actor_id: actor, roles: roles.length ? roles : [\"operator\"] };
    }

    function write(line, cls = \"\") {
      const div = document.createElement(\"div\");
      if (cls) div.className = cls;
      div.textContent = line;
      $(\"terminal\").appendChild(div);
      $(\"terminal\").scrollTop = $(\"terminal\").scrollHeight;
    }

    function writeBanner() {
      write(\" ____             _       ____       _           \");
      write(\"| __ ) _ __ __ _(_)_ __ |  _ \\\\ _ __(_)_   _____ \");
      write(\"|  _ \\\\| '__/ _` | | '_ \\\\| | | | '__| \\\\ \\\\ / / _ \\\\\");
      write(\"| |_) | | | (_| | | | | | |_| | |  | |\\\\ V /  __/\");
      write(\"|____/|_|  \\\\__,_|_|_| |_|____/|_|  |_| \\\\_/ \\\\___| v0.1\");
    }

    async function get(path) {
      const res = await fetch(path);
      return await res.json();
    }

    async function post(path, body) {
      const res = await fetch(path, {
        method: \"POST\",
        headers: { \"Content-Type\": \"application/json\" },
        body: JSON.stringify(body),
      });
      return await res.json();
    }

    function renderResponse(data) {
      if (data.intent === \"error\") {
        const err = (data.payload || {}).error || {};
        write(`[${err.code || \"E_NODE_ERROR\"}] ${err.message || \"error\"}`, \"err\");
        return;
      }
      if (data.intent === \"web.console.session.approval_required\") {
        state.pendingRequestId = data.payload.approval_request_id || \"\";
        write(`Approval required: ${state.pendingRequestId}`, \"warn\");
        return;
      }
      if (data.intent === \"web.console.session.events\") {
        const events = (data.payload || {}).events || [];
        for (const evt of events) {
          const p = evt.payload || {};
          if (typeof p.data === \"string\" && p.data.length) {
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
      const roles = encodeURIComponent(actor.roles.join(\",\"));
      const data = await get(`/webterm/targets?actor_id=${q}&roles=${roles}`);
      if (data.intent === \"web.console.targets\") {
        $(\"target\").innerHTML = \"\";
        for (const t of data.payload.targets || []) {
          const o = document.createElement(\"option\");
          o.value = t;
          o.textContent = t;
          $(\"target\").appendChild(o);
        }
        const def = data.payload.default_target || \"\";
        if (def) $(\"target\").value = def;
      } else {
        renderResponse(data);
      }
    }

    async function openSession(options = {}) {
      const retryOnPolicyDeny = options.retryOnPolicyDeny !== false;
      if (state.opening) return false;
      state.opening = true;
      try {
        if (state.sessionId) {
          await closeSession({ silent: true });
        }

        const actor = actorPayload();
        const data = await post(\"/webterm/session/open\", {
          ...actor,
          origin: $(\"origin\").value.trim() || window.location.origin,
          target: $(\"target\").value,
        });

        if (data.intent === \"web.console.session.ready\") {
          setSessionState(data.payload.session_id || \"\", actor.actor_id);
          write(data.payload.banner || \"session ready\", \"ok\");
          return true;
        }

        renderResponse(data);
        const err = (data.payload || {}).error || {};
        const code = String(err.code || \"\").trim();
        if (retryOnPolicyDeny && code === \"E_WEBTERM_POLICY_DENIED\") {
          const rotated = rotateActorIdentity();
          write(`[warn] switched actor to ${rotated} and retrying open`, \"warn\");
          await loadTargets();
          state.opening = false;
          return await openSession({ retryOnPolicyDeny: false });
        }
        return false;
      } finally {
        state.opening = false;
      }
    }

    async function closeSession(options = {}) {
      const silent = options.silent === true;
      const actor = actorPayload();
      const current = state.sessionId;
      if (!current) {
        setSessionState(\"\", actor.actor_id);
        return true;
      }

      const data = await post(\"/webterm/session/close\", {
        ...actor,
        session_id: current,
      });
      if (!silent) {
        renderResponse(data);
      }
      setSessionState(\"\", actor.actor_id);
      return data.intent !== \"error\";
    }

    async function tryResumeStoredSession() {
      const actor = actorPayload();
      const storedSessionId = (localStorage.getItem(actorSessionStorageKey(actor.actor_id)) || \"\").trim();
      if (!storedSessionId) {
        return false;
      }

      setSessionState(storedSessionId, actor.actor_id);
      write(`[startup] resuming session ${storedSessionId}...`);
      const probe = await post(\"/webterm/message\", {
        ...actor,
        session_id: storedSessionId,
        text: \"/health\",
      });

      if (probe.intent === \"web.console.session.events\") {
        write(\"[ok] session resumed\", \"ok\");
        renderResponse(probe);
        return true;
      }

      write(\"[warn] stored session is no longer valid\", \"warn\");
      setSessionState(\"\", actor.actor_id);
      return false;
    }

    async function sendCommand(confirmRetry = false) {
      if (!state.sessionId) {
        write(\"Open a session first.\", \"warn\");
        return;
      }
      const cmd = (confirmRetry ? state.lastCommand : $(\"cmd-input\").value).trim();
      if (!cmd) return;
      state.lastCommand = cmd;
      if (!confirmRetry) $(\"cmd-input\").value = \"\";

      write(`> ${cmd}`, \"ok\");
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

      const data = await post(\"/webterm/message\", body);
      if (confirmRetry && data.intent !== \"web.console.session.approval_required\") {
        state.pendingRequestId = \"\";
      }
      renderResponse(data);
      if (data.intent === \"error\") {
        const err = (data.payload || {}).error || {};
        const code = String(err.code || \"\").trim();
        if (code === \"E_WEBTERM_SESSION_EXPIRED\") {
          const actor = actorPayload();
          setSessionState(\"\", actor.actor_id);
        }
      }
    }

    window.addEventListener(\"DOMContentLoaded\", async () => {
      $(\"origin\").value = window.location.origin;
      writeBanner();
      ensureActorIdentity();
      write(\"[startup] checking web console health...\");
      try {
        const health = await get(\"/webterm/health\");
        if (health.ok) {
          write(\"[ok] gateway.webterm healthy\", \"ok\");
        } else {
          write(\"[warn] gateway.webterm health check failed\", \"warn\");
        }
      } catch (err) {
        write(`[warn] health check failed: ${String(err)}`, \"warn\");
      }
      await loadTargets();
      $(\"open-btn\").addEventListener(\"click\", openSession);
      $(\"close-btn\").addEventListener(\"click\", closeSession);
      $(\"send-btn\").addEventListener(\"click\", () => sendCommand(false));
      $(\"approve-btn\").addEventListener(\"click\", () => sendCommand(true));
      $(\"cmd-input\").addEventListener(\"keydown\", (e) => {
        if (e.key === \"Enter\") {
          e.preventDefault();
          sendCommand(false);
        }
      });
      for (const btn of document.querySelectorAll(\".quick\")) {
        btn.addEventListener(\"click\", () => {
          $(\"cmd-input\").value = btn.getAttribute(\"data-cmd\") || \"\";
          sendCommand(false);
        });
      }
      const resumed = await tryResumeStoredSession();
      if (!resumed) {
        write(\"[startup] opening session...\");
        await openSession();
      }
    });
  </script>
</body>
</html>
"""
