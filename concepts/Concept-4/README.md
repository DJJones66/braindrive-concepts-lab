# Concept-4: Unified Skill Node via Docker Compose

Concept-4 starts from Concept-3 and replaces dedicated interview/spec/plan nodes with one unified `node-workflow-skill`.

## Service Topology

- `node-router` (`router.core`): dynamic node registry, metadata-driven deterministic routing, provider pinning.
- `intent-router-natural-language` (`intent.router.nl`): NL intent analysis and route orchestration.
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
  - `node-audit-log`

## Ports

- Router API: `http://localhost:9480`
- Intent router API: `http://localhost:9481`

(override in `.env` with `BRAINDRIVE_ROUTER_PORT` / `BRAINDRIVE_INTENT_PORT`)

## Quick Start

```bash
cd /home/hacker/Projects/BrainDrive-Protocal/Concept_Containers/Concept-4
cp .env.example .env
docker compose up -d
```

## Terminal CLI App

Run the interactive terminal app:

```bash
cd /home/hacker/Projects/BrainDrive-Protocal/Concept_Containers/Concept-4
python scripts/cli.py
```

or:

```bash
./scripts/cli.sh
```

CLI notes:

- CLI loads `Concept-4/.env` automatically for local runs (without overriding already-exported env vars).
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
- Interactive startup prints a blue BrainDrive ASCII banner before health checks.
- For operations requiring approval, CLI prompts for confirmation interactively.
- If intent routing cannot map your prompt to a specific workflow/tool action, it defaults to model chat (`model.chat.complete`).
- `model.chat.complete` and `model.chat.stream` call provider APIs (`/chat/completions`) through `node-model-openrouter` or `node-model-ollama`.
- `workflow.interview.*`, `workflow.spec.*`, and `workflow.plan.*` are now handled by `node-workflow-skill` (with legacy intent compatibility).
- New generic capabilities are also available: `skill.catalog.list`, `skill.execute.read`, `skill.execute.stateful`, `skill.execute.mutate`, `skill.execute.destructive`.

Single-message mode:

```bash
python scripts/cli.py --message "Create folder for my finances" --confirm
```

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
cd /home/hacker/Projects/BrainDrive-Protocal/Concept_Containers/Concept-4
python -m pytest
```

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
- If you get `Permission denied` editing `data/...` files from host, fix ownership once:

```bash
docker compose run --rm --entrypoint sh node-router -lc 'chown -R 1001:1001 /workspace/data'
```

- Compose runs services as `HOST_UID:HOST_GID` (defaults `1001:1001`). Set these in `.env` to match your host user when needed.

## Stop

```bash
docker compose down
```
