# Concept-4 Work Completed

Date: 2026-02-26

## Objective
Create `Concept-4` from `Concept-3`, remove separate workflow skill nodes (`interview/spec/plan`), replace them with one unified Skill Node, and verify interview/spec/plan flow parity.

## Implementation Summary

1. Baseline copy
- Copied all files from `Concept_Containers/Concept-3` into `Concept_Containers/Concept-4`.

2. Unified skill node implementation
- Added `braindrive_runtime/nodes/skill.py` implementing `SkillWorkflowNode` with:
  - Generic capabilities:
    - `skill.catalog.list`
    - `skill.execute.read`
    - `skill.execute.stateful`
    - `skill.execute.mutate`
    - `skill.execute.destructive`
  - Legacy compatibility capabilities:
    - `workflow.interview.start|continue|complete`
    - `workflow.spec.generate|propose_save`
    - `workflow.plan.generate|propose_save`
  - Action execution + legacy adapters.
  - Skill catalog hot-reload from `.braindrive/skills`.
  - Observability events:
    - `skill.catalog.loaded`
    - `skill.execution.started`
    - `skill.execution.completed`
    - `skill.execution.failed`
  - Session/output persistence under workflow state:
    - `skill_sessions[skill_id][folder]`
    - `skill_outputs[skill_id][folder]`

3. Removed dedicated workflow node implementations
- Deleted:
  - `braindrive_runtime/nodes/interview.py`
  - `braindrive_runtime/nodes/spec.py`
  - `braindrive_runtime/nodes/plan.py`

4. Runtime/service wiring updated to single node
- Updated imports/registration in:
  - `braindrive_runtime/nodes/__init__.py`
  - `braindrive_runtime/runtime.py`
  - `services/node_service.py`
- `NODE_KIND=skill` is now the workflow skill runtime target.

5. Docker Compose topology updated
- Updated `docker-compose.yml`:
  - Removed services:
    - `node-workflow-interview`
    - `node-workflow-spec`
    - `node-workflow-plan`
  - Added/kept single service:
    - `node-workflow-skill`
- Updated Concept-4 defaults:
  - ports: `BRAINDRIVE_ROUTER_PORT=9480`, `BRAINDRIVE_INTENT_PORT=9481`
  - default token: `concept4-dev-token`
  - app name default: `BrainDrive Concept-4`

6. Bootstrap and skill package layout support
- Updated `braindrive_runtime/nodes/runtime_bootstrap.py` to provision both:
  - legacy markdown skills (compatibility)
  - manifest/prompt directory layout (`skill.yaml` + `prompts/*.md`) for interview/spec-generation/plan-generation.

7. Scripts/docs updates
- Updated:
  - `README.md`
  - `.env.example`
  - `Test-1.md`
  - `scripts/cli.py` (`BRAINDRIVE_*` env vars)
  - `scripts/demo.py` (`BRAINDRIVE_*` env vars)
  - `pyproject.toml`
  - `braindrive_runtime/__init__.py`

8. Added tests for unified node
- Added `tests/test_skill_node.py` covering:
  - skill catalog listing
  - legacy capability mapping to `node.workflow.skill`
  - generic `skill.execute.stateful` behavior
  - tier mismatch validation (`E_BAD_MESSAGE`)

## Validation Runbook Executed

1. Unit test suite
- Command: `python -m pytest -q`
- Result: **50 passed**

2. Compose build/run
- Command: `docker compose up -d --build`
- Result: all services running

3. End-to-end workflow (NL)
- Executed interview -> spec -> plan sequence via `POST /intent/route`.
- Applied approval flows for `save spec` and `save plan`.
- Verified artifacts and git commits in `data/library/single-skill-node/`.
- Verified router dispatch to `node.workflow.skill` for workflow intents.

4. Detailed test evidence
- See `Test-Results.md` for full prompt table and outcomes.

## Notes
- One transient provider-side `E_NODE_UNAVAILABLE` occurred on first `save plan` attempt; retry succeeded and full flow completed.
- This did not indicate routing/safety regression in the unified node.

## Post-Migration CLI Updates (2026-02-26)

1. Prompt and color controls moved to `.env`
- Added prompt-specific color keys:
  - `BRAINDRIVE_CLI_COLOR_PROMPT_APP`
  - `BRAINDRIVE_CLI_COLOR_PROMPT_FOLDER`
  - `BRAINDRIVE_CLI_COLOR_PROMPT_ARROW`
- Added automatic `.env` load in `scripts/cli.py` for local runs (without overriding already-exported env vars).

2. Command discovery improvements
- Added `/commands <word>` search command (with `/command <word>` alias).
- Added usage hints for example prompts where required fields are known.

3. Real token streaming for fallback model chat
- Added real-time streaming output in `scripts/cli.py` for fallback `model.chat.*` responses.
- Added controls in `.env`:
  - `BRAINDRIVE_CLI_STREAM_MODEL_CHAT`
  - `BRAINDRIVE_CLI_STREAM_FALLBACK_ONLY`
  - `BRAINDRIVE_CLI_STREAM_DIAGNOSTICS`
- Added `context` support to intent analysis endpoint and enabled `/intent/analyze` in service runtime so CLI can decide streamability before routing.
