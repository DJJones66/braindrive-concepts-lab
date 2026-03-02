# Concept-3: Protocol Plan via Docker Compose

Concept-3 implements `Pivot/Build-Plan/Protocol-Plan.md` using the same Docker Compose service style as Concept-1/Concept-2.

## Service Topology

- `node-router` (`router.core`): dynamic node registry, metadata-driven deterministic routing, provider pinning.
- `intent-router-natural-language` (`intent.router.nl`): NL intent analysis and route orchestration.
- Node services:
  - `node-runtime-bootstrap`
  - `node-memory-fs`
  - `node-workflow-folder`
  - `node-workflow-interview`
  - `node-workflow-spec`
  - `node-workflow-plan`
  - `node-approval-gate`
  - `node-git-ops`
  - `node-model-openrouter`
  - `node-model-ollama`
  - `node-chat-general`
  - `node-audit-log`

## Ports

- Router API: `http://localhost:9380`
- Intent router API: `http://localhost:9381`

(override in `.env` with `CONCEPT3_ROUTER_PORT` / `CONCEPT3_INTENT_PORT`)

## Quick Start

```bash
cd /home/hacker/Projects/BrainDrive-Protocal/Concept_Containers/Concept-3
cp .env.example .env
docker compose up -d
```

## Terminal CLI App

Run the interactive terminal app:

```bash
cd /home/hacker/Projects/BrainDrive-Protocal/Concept_Containers/Concept-3
python scripts/cli.py
```

or:

```bash
./scripts/cli.sh
```

CLI notes:

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
  - Override with `CONCEPT3_CLI_HISTORY_FILE=/path/to/history`.
  - Configure max entries with `CONCEPT3_CLI_HISTORY_MAX` (default `2000`).
- Prompt colors are enabled automatically on TTY terminals.
  - `braindrive` renders blue and active folder renders green.
  - Override with `CONCEPT3_CLI_COLOR=on|off|auto` (default `auto`).
- Interactive startup prints a blue BrainDrive ASCII banner before health checks.
- For operations requiring approval, CLI prompts for confirmation interactively.
- If intent routing cannot map your prompt to a specific workflow/tool action, it defaults to model chat (`model.chat.complete`).
- `model.chat.complete` and `model.chat.stream` call provider APIs (`/chat/completions`) through `node-model-openrouter` or `node-model-ollama`.
- `workflow.interview.*`, `workflow.spec.*`, and `workflow.plan.*` are skill+LLM driven and depend on model-node availability.

Single-message mode:

```bash
python scripts/cli.py --message "Create folder for my finances" --confirm
```

## Health Checks

```bash
curl -s http://localhost:9380/health | jq
curl -s http://localhost:9381/health | jq
```

## Example Intent Route

```bash
curl -s http://localhost:9381/intent/route \
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

## Local Tests (non-docker)

```bash
cd /home/hacker/Projects/BrainDrive-Protocal/Concept_Containers/Concept-3
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
