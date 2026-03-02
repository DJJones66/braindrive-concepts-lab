# Concept-2 Work Completed

Date: 2026-02-25

## Scope Delivered

Built a copy-forward Concept-2 environment in:

- `/home/hacker/Projects/BrainDrive-Protocal/Concept_Containers/Concept-2`

Baseline behavior from Concept-1 was preserved and extended without modifying Concept-1.

Concept-2 adds:

1. A fake capability node (`node.markdown.library`) with markdown-file operations.
2. A human-in-the-loop web testing interface for intent analysis and routing.
3. Intent-router API expansion for analysis and capability introspection.

## Core Additions

### 1) New fake capability node: `node.markdown.library`

File:

- `nodes/markdown_library/markdown_library_service.py`

Capabilities implemented:

- `md.library.list_notes`
- `md.library.read_note`
- `md.library.create_note`
- `md.library.append_note`
- `md.library.search_notes`
- `md.library.delete_note`

Behavior:

- self-registers to router and heartbeats like other nodes
- uses a local markdown workspace under `data/markdown-library/`
- seeds starter files for immediate testing
- enforces required extensions per capability
- requires confirmation for destructive delete intent
- records local JSONL event logs

### 2) Intent-router extensions for human testing

File:

- `intent_router/intent_router_service.py`

Added behavior:

- new intent mapping rules for markdown capabilities
- `/intent/analyze` endpoint (intent analysis only, no dispatch)
- `/intent/capabilities` endpoint (reads live router catalog, filtered fake capabilities)
- UI hosting at `/ui` and `/`
- route responses now include analysis payload for easier inspection

### 3) Human-in-the-loop web UI

File:

- `intent_router/static/intent_lab.html`

UI includes:

- capability panel populated from live router catalog
- NL input and scenario chips
- identity/authz/confirmation controls
- analyze-only mode vs analyze-and-route mode
- output summary showing intent/confidence/risk/reason codes/required extensions
- raw JSON response panel for debugging

### 4) Compose updates

File:

- `docker-compose.yml`

Changes:

- added `node-markdown-library` service
- switched host port defaults to Concept-2 set (`9280/9281/9282`)
- updated router mutating/destructive intent lists for markdown fake capabilities
- retained all Concept-1 support services for integration testing

### 5) Demo and docs updates

Files:

- `scripts/demo.py`
- `scripts/demo.sh`
- `.env.example`
- `README.md`
- `WORK-COMPLETED.md`

Demo now validates:

1. dynamic registration with new node count
2. fake capability catalog visibility
3. intent lab UI availability
4. `/intent/analyze` mapping output
5. markdown create/append/read route execution
6. fail-closed policy behavior under auth outage

## Notes on architecture intent

- Fake capabilities are executed by a **separate node** (`node.markdown.library`).
- Intent mapping rules for those capabilities are implemented in `intent.router.natural-language`.
- UI capability display is sourced from live router catalog, not from a static hardcoded list.

## Data outputs

Generated runtime artifacts (when running):

- `data/router/router-events.jsonl`
- `data/intent/intent-events.jsonl`
- `data/audit/audit-events.jsonl`
- `data/activity/activity-events.jsonl`
- `data/workflow/workflow-events.jsonl`
- `data/markdown-library/events.jsonl`
- `data/markdown-library/*.md`
