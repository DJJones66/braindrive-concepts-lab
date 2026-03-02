# Concept-2: Human-in-the-Loop Intent Lab (Docker Compose)

Concept-2 is a copy-forward extension of Concept-1.

- Concept-1 remains unchanged as the prior baseline.
- Concept-2 adds fake capability testing with a markdown-library node.
- Concept-2 adds a web UI for manual human testing of intent mapping.

## What Concept-2 Adds

## 1) Fake Capability Node

New node:

- `node-markdown-library` (`node.markdown.library`)

Fake capabilities exposed by this node:

- `md.library.list_notes`
- `md.library.read_note`
- `md.library.create_note`
- `md.library.append_note`
- `md.library.search_notes`
- `md.library.delete_note`

The node operates on local markdown files under:

- `data/markdown-library/`

This enables lightweight but realistic filesystem-style operations for intent experiments.

## 2) Intent Lab Web UI

`intent.router.natural-language` now serves a web page at:

- `GET /ui`

UI features:

- live fake capability list from router catalog
- natural-language input box
- action mode selector:
  - analyze only
  - analyze and route
- identity/authz/confirmation controls
- output panel showing:
  - canonical intent
  - confidence
  - risk class
  - reason codes
  - required extensions
  - raw JSON response

## 3) New Intent API Surfaces

From `intent.router.natural-language`:

- `POST /intent/analyze`
  - analyze only (no dispatch)
- `GET /intent/capabilities`
  - returns fake capability summary + full catalog
- existing `POST /intent/plan` and `POST /intent/route` remain available

## Services in This Compose

- `node-router`
- `intent-router-natural-language`
- `node-auth-policy`
- `node-session-state`
- `node-audit-log`
- `node-activity-feedback`
- `node-chat-general-primary`
- `node-chat-general-backup`
- `node-workflow`
- `node-markdown-library` (new)

## Ports

- Router API: `http://localhost:9280`
- Intent API + UI: `http://localhost:9281`
- Auth policy control API: `http://localhost:9282`

## Quick Start

```bash
cd /home/hacker/Projects/BrainDrive-Protocal/Concept_Containers/Concept-2
cp .env.example .env
docker compose up -d
```

Open the web lab:

- `http://localhost:9281/ui`

## Human Testing Flow (UI)

1. Enter a natural-language instruction in the prompt box.
2. Use `Analyze only` to inspect mapped intent/confidence without executing.
3. Use `Analyze and route` to execute through router and target node.
4. For mutating/destructive actions, set `Confirmation=true`.
5. Inspect JSON output for full trace of planner and route responses.

Example test phrases:

- `List markdown notes`
- `Read note q2-roadmap`
- `Create note called release-checklist with deployment tasks`
- `Append to note release-checklist with add rollback verification`
- `Search notes for roadmap`
- `Delete markdown note release-checklist`

## CLI/Script Validation

Run full automated validation:

```bash
./scripts/demo.sh
```

This validates:

1. dynamic registration
2. fake capability catalog visibility
3. UI availability
4. analyze endpoint behavior
5. create/append/read markdown note flows
6. fail-closed behavior under policy outage

## Data and Logs

- `data/router/router-events.jsonl`
- `data/intent/intent-events.jsonl`
- `data/audit/audit-events.jsonl`
- `data/activity/activity-events.jsonl`
- `data/workflow/workflow-events.jsonl`
- `data/markdown-library/events.jsonl`
- `data/markdown-library/*.md` (fake note files)

## Stop

```bash
docker compose down
```
