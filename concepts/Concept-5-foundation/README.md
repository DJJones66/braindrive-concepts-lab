# BrainDrive-MVP: Unified Skill Node via Docker Compose

BrainDrive-MVP consolidates dedicated interview/spec/plan nodes into one unified `node-workflow-skill`.

## Service Topology

- `node-router` (`router.core`): dynamic node registry, metadata-driven deterministic routing, provider pinning.
- `intent-router-natural-language` (`intent.router.nl`): NL intent analysis and route orchestration.
- `gateway` (`gateway.api`): canonical ingress for `/api/v1/*` and gateway-owned web console routes (`/ui/terminal`, `/webterm/*`) on gateway port.
- Node services:
  - `node-runtime-bootstrap`
  - `node-memory-fs`
  - `node-workflow-folder`
  - `node-workflow-skill`
  - `node-approval-gate`
  - `node-git-ops`
  - `node-model-openrouter`
  - `node-model-ollama`
  - `node-chat-general`
  - `node-web-scrapling`
  - `node-web-console`
  - `node-web-console-tty` (dedicated CLI-style browser terminal frontend using `ttyd`)
  - `node-audit-log`
- Dev-only profile services:
  - `dev-web-terminal` (browser terminal with auth; starts BrainDrive CLI first, then raw shell)

## Ports

- Router API: `http://localhost:9480`
- Intent router API: `http://localhost:9481`
- Gateway API: `http://localhost:9482`
- Dedicated CLI-style browser terminal (`ttyd`): `http://localhost:9493`
- Gateway-owned web console UI/API: `http://localhost:9482/ui/terminal`
- Dev web terminal (profile `dev`): `http://localhost:9494`

(override in `.env` with `BRAINDRIVE_ROUTER_PORT` / `BRAINDRIVE_INTENT_PORT`)

## Quick Start

```bash
cd /home/hacker/Projects/BrainDrive-Protocal/BrainDrive-MVP
./scripts/bootstrap.sh
```

`bootstrap.sh` will:

- create `.env` from `.env.example` if missing
- ensure `data/runtime` and `data/library` exist
- start compose with your current host `uid:gid` (no manual `.env` edit needed)
- derive `NETWORK_BIND_ADDR` from `NETWORK_EXPOSED` (`true` -> `0.0.0.0`, `false` -> `127.0.0.1`)
- generate and persist a unique `ROUTER_REGISTRATION_TOKEN` in `.env` when missing/insecure (dev mode)
- wait for `router.core` and `intent.router.natural-language` health endpoints

Manual startup (if preferred):

```bash
cd /home/hacker/Projects/BrainDrive-Protocal/BrainDrive-MVP
cp -n .env.example .env
NETWORK_EXPOSED=true  # set false for localhost-only publish
NETWORK_BIND_ADDR=$([ "${NETWORK_EXPOSED}" = "true" ] && echo "0.0.0.0" || echo "127.0.0.1")
ROUTER_REGISTRATION_TOKEN=$(openssl rand -hex 24)
HOST_UID=$(id -u) HOST_GID=$(id -g) NETWORK_BIND_ADDR="${NETWORK_BIND_ADDR}" ROUTER_REGISTRATION_TOKEN="${ROUTER_REGISTRATION_TOKEN}" docker compose up -d --build
```

## Terminal CLI App

Run the interactive terminal app:

```bash
cd /home/hacker/Projects/BrainDrive-Protocal/BrainDrive-MVP
python scripts/cli.py
```

or:

```bash
./scripts/cli.sh
```

CLI notes:

- CLI loads `BrainDrive-MVP/.env` automatically for local runs (without overriding already-exported env vars).
- It checks service health, runs startup bootstrap, and initializes git if needed.
- Type normal language prompts directly (for example: `Create folder for my finances`).
- Use `/help` for CLI commands.
- Use `/prompts` to list prompt sections from live capability metadata.
- Use `/prompts <section>` (for example `/prompts workflow`) for section details.
- Use `/prompts next` to continue paged output.
- Use `/prompts all` for a paged full listing.
- Use `/clear` to clear the terminal and replay startup view.
- During interview flow, normal replies are treated as interview answers automatically (no `answer:` prefix required).
- Arrow keys work for command history/navigation (`readline` enabled).
- History persists across sessions at `data/runtime/state/.cli_history`.
  - Override with `BRAINDRIVE_CLI_HISTORY_FILE=/path/to/history`.
  - Configure max entries with `BRAINDRIVE_CLI_HISTORY_MAX` (default `2000`).
- Prompt colors are enabled automatically on TTY terminals.
  - `braindrive` renders blue and active folder renders green.
  - Override with `BRAINDRIVE_CLI_COLOR=on|off|auto` (default `auto`).
  - Prompt parts can be tuned with:
    `BRAINDRIVE_CLI_COLOR_PROMPT_APP`,
    `BRAINDRIVE_CLI_COLOR_PROMPT_FOLDER`,
    `BRAINDRIVE_CLI_COLOR_PROMPT_ARROW`.
  - Other CLI colors can be tuned with:
    `BRAINDRIVE_CLI_COLOR_SYSTEM`,
    `BRAINDRIVE_CLI_COLOR_AI`,
    `BRAINDRIVE_CLI_COLOR_BANNER`,
    `BRAINDRIVE_CLI_COLOR_VERSION`.
- Fallback model chat output streams real tokens in real time (not simulated typing).
  - Toggle with `BRAINDRIVE_CLI_STREAM_MODEL_CHAT=true|false` (default `true`).
  - Restrict streaming to fallback chat only with `BRAINDRIVE_CLI_STREAM_FALLBACK_ONLY=true|false` (default `true`).
  - Verification mode: `BRAINDRIVE_CLI_STREAM_DIAGNOSTICS=true` prints
    provider/model plus `ttft`, chunk count, and total time after each streamed reply.
  - Streaming uses provider credentials available in the running CLI environment.
    If you update `.env`, restart/rebuild containers (`./scripts/bootstrap.sh`) before retesting streaming.
  - Foundation default keeps gateway-only ingress. CLI fallback to `/intent/route` is opt-in:
    `BRAINDRIVE_CLI_ALLOW_INTENT_FALLBACK=false` (set `true` only for compatibility windows).
- Gateway auth default is fail-closed (`GATEWAY_ENFORCE_SESSION=true`).
- CLI now establishes a gateway auth session on startup using `/api/v1/auth/*`.
  - Default auth username/password are derived deterministically from
    `BRAINDRIVE_CLI_AUTH_SEED` or `ROUTER_REGISTRATION_TOKEN`.
  - Override with `BRAINDRIVE_CLI_AUTH_USERNAME` and `BRAINDRIVE_CLI_AUTH_PASSWORD`.
  - Control auto-provision behavior with `BRAINDRIVE_CLI_AUTH_AUTO_REGISTER=true|false`.
- Interactive startup prints a blue BrainDrive ASCII banner before health checks.
- For operations requiring approval, CLI prompts for confirmation interactively.
- If intent routing cannot map your prompt to a specific workflow/tool action, it defaults to model chat (`model.chat.complete`).
- `model.chat.complete` and `model.chat.stream` call provider APIs (`/chat/completions`) through `node-model-openrouter` or `node-model-ollama`.
  - Provider transport/mapping lives in `braindrive_runtime/providers/*`; model nodes are adapter-wired only.
- `workflow.interview.*`, `workflow.spec.*`, and `workflow.plan.*` are now handled by `node-workflow-skill` (with legacy intent compatibility).
- New generic capabilities are also available: `skill.catalog.list`, `skill.execute.read`, `skill.execute.stateful`, `skill.execute.mutate`, `skill.execute.destructive`.

Single-message mode:

```bash
python scripts/cli.py --message "Create folder for my finances" --confirm
```

## Dev Web Terminal (isolated container + BrainDrive CLI)

This is a separate, dev-only browser terminal service with basic auth.

Isolation model:

1. No project workspace bind mount is exposed to this container.
2. It communicates with BrainDrive services over HTTP (`node-router` and `intent-router-natural-language`).
3. It mounts only:
   - `./data/runtime/dev-webterm` (CLI history/session-local runtime files)
   - `./data/library` read-only

Behavior:

1. Opens directly into `python -u scripts/cli.py` (natural language BrainDrive interaction).
2. When you exit CLI (`/quit`), it drops into a raw interactive shell in the same container.
3. From raw shell, re-enter CLI at any time with:

```bash
python -u scripts/cli.py
```

Enable and start it:

```bash
cd /home/hacker/Projects/BrainDrive-Labs/braindrive-concepts-lab/concepts/Concept-5
docker compose --profile dev up -d --build dev-web-terminal
```

Open in browser:

- URL: `http://localhost:9494`
- Username: `DEV_WEBTERM_AUTH_USER` (default `dev`)
- Password: `DEV_WEBTERM_AUTH_PASSWORD` (default `change-me-now`)

Set custom credentials/port in `.env` before use:

```bash
DEV_WEBTERM_AUTH_USER=devadmin
DEV_WEBTERM_AUTH_PASSWORD=replace-with-strong-password
BRAINDRIVE_DEV_WEBTERM_PORT=9494
DEV_WEBTERM_LOG_LEVEL=2
DEV_WEBTERM_THEME_BACKGROUND=#000000
```

Exposure control notes:

1. Localhost-only publish for all host ports:
```bash
NETWORK_EXPOSED=false
```
2. Expose published ports on all interfaces:
```bash
NETWORK_EXPOSED=true
```
3. Advanced override (specific interface only):
```bash
NETWORK_BIND_ADDR=10.1.2.149
```
4. CIDR/IP allowlisting is best enforced at host firewall/reverse-proxy layer.
5. When `NETWORK_BIND_ADDR` is non-loopback, web terminal services refuse startup if password remains `change-me-now`.

## Dedicated CLI-Style Browser Terminal on 9493

This service provides a browser terminal that looks/behaves like a normal terminal session
while running `python -u scripts/cli.py` first, then dropping to a raw shell on exit.

Access:

- URL: `http://localhost:9493`
- Service: `node-web-console-tty`
- Uses `ttyd` and is independent from gateway `/ui/terminal` UI implementation.

Auth:

1. Disabled by default for local comparison workflows.
2. Enable basic auth by setting in `.env`:

```bash
TTY_WEBTERM_AUTH_ENABLED=true
TTY_WEBTERM_AUTH_USER=devadmin
TTY_WEBTERM_AUTH_PASSWORD=replace-with-strong-password
```

Display tuning:

```bash
TTY_WEBTERM_LOG_LEVEL=2
TTY_WEBTERM_THEME_BACKGROUND=#000000
```

## Gateway Web Console Target Resolution

`/ui/terminal` target resolution is discovery-first:

1. Discover active targets from router registry.
2. Apply optional allowlist (`WEBTERM_TARGETS`) and denylist (`WEBTERM_TARGETS_DENYLIST`).
3. Select target by order: explicit request target, then active configured default, then first discovered target.

Deployment knobs:

- `WEBTERM_TARGET_DISCOVERY_ENABLED=true|false`
- `WEBTERM_TARGET_DISCOVERY_TTL_SEC`
- `WEBTERM_TARGET_DISCOVERY_TIMEOUT_SEC`
- `WEBTERM_REQUIRE_EXPLICIT_BASE_URLS=true|false` (recommended `true` for non-dev environments)

## Health Checks

```bash
curl -s http://localhost:9480/health | jq
curl -s http://localhost:9481/health | jq
```

## Example Intent Route

```bash
curl -s http://localhost:9481/intent/route \
  -H 'Content-Type: application/json' \
  -d '{"message":"Create folder for my finances","confirm":true}' | jq
```

## Scrapling Output Persistence

Scrape responses now save to the library by default:

1. default directory: `data/library/scraping`
2. directory is auto-created on first scrape
3. response payload includes `payload.storage` with saved file paths

Config:

```bash
BRAINDRIVE_SCRAPLING_DEFAULT_SAVE=true
BRAINDRIVE_SCRAPLING_DEFAULT_SAVE_DIR=scraping
```

Per-request override in payload:

1. disable save: `save_to_library=false`
2. custom subdirectory: `save_directory=\"my-scrapes\"`

## Test/Debug Endpoints

Enabled only when `.env` sets:

```bash
BRAINDRIVE_ENABLE_TEST_ENDPOINTS=true
```

When enabled:

- `POST /intent/analyze`
- `GET /intent/capabilities`
- `POST /intent/test-route`

## Workflow Full Trace Log

Control full `/intent/route` trace records in `data/runtime/logs/workflow.jsonl`:

```bash
BRAINDRIVE_WORKFLOW_FULL_TRACE=true
```

- `true` (default): logs `request` + `response` (including `analysis`, `route_message`, `route_response`).
- `false`: keeps `workflow.jsonl` focused on workflow-domain events only.

## Local Tests (non-docker)

```bash
cd /home/hacker/Projects/BrainDrive-Protocal/BrainDrive-MVP
python -m pytest
```

## Conformance Suite

Run D147-style config-only swap conformance:

```bash
cd /home/hacker/Projects/BrainDrive-Protocal/BrainDrive-MVP
BRAINDRIVE_CONFORMANCE_REPORT=./data/runtime/conformance-report.json \
python -m pytest tests/conformance/test_swap_conformance.py
```

Notes:

- Covers provider swap (OpenRouter <-> Ollama), model override swap, and tool backend swap.
- Asserts swaps are config/backend only (no source edits required).
- Writes machine-readable JSON report to `BRAINDRIVE_CONFORMANCE_REPORT`.

## Troubleshooting

- If model replies just echo your prompt (for example `[openrouter:model] hello`), you are running an older container image with stub model nodes.
- Rebuild and restart:

```bash
docker compose down
docker compose up -d --build
```

- If model requests fail with router-level timeout errors, increase `ROUTER_NODE_TIMEOUT_SEC` (default `45`) in `.env`, then restart compose.
- If you see intermittent CLI transport errors while model calls are slow, increase `INTENT_ROUTER_ROUTE_TIMEOUT_SEC` (default `60`) in `.env`, then restart compose.
- If interview/spec/plan generation fails, verify provider config and model-node health first (`/health` command in CLI).
- For new machines, prefer `./scripts/bootstrap.sh` so compose always uses your host `uid:gid`.
- If you get `Permission denied` editing `data/...` files from host, fix ownership once:

```bash
docker compose run --rm --entrypoint sh node-router -lc "chown -R $(id -u):$(id -g) /workspace/data"
```

- Compose runs services as `HOST_UID:HOST_GID` (defaults `1001:1001`).

## Stop

```bash
docker compose down
```
