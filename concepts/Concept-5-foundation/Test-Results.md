# BrainDrive-MVP Unified Skill Node Test Results

Date: 2026-02-26
Environment: docker compose stack in `BrainDrive-MVP` (ports `9480/9481`), provider `openrouter`
Execution mode: scripted NL workflow via `POST /intent/route` + approval apply flow

## Scope Tested
- Unified `node.workflow.skill` replacing dedicated interview/spec/plan nodes.
- Legacy intent compatibility:
  - `workflow.interview.*`
  - `workflow.spec.*`
  - `workflow.plan.*`
- End-to-end approval/write/commit behavior for `save spec` and `save plan`.

## Pre-checks
- `docker compose up -d --build` completed successfully.
- `docker compose ps` reported all BrainDrive-MVP services `Up`.
- Local automated tests passed: `python -m pytest -q` -> **50 passed**.

## Workflow Results

| # | Prompt | Canonical Intent | Route Response | Result |
|---|---|---|---|---|
| 1 | `create folder for single skill node` | `folder.create` | `folder.created` | PASS |
| 2 | `switch folder to single-skill-node` | `folder.switch` | `folder.switched` | PASS |
| 3 | `start interview` | `workflow.interview.start` | `workflow.interview.question` | PASS |
| 4 | `answer: I want a unified skill node that can run interview/spec/plan` | `workflow.interview.continue` | `workflow.interview.question` | PASS |
| 5 | `answer: success means one node handles legacy workflow intents` | `workflow.interview.continue` | `workflow.interview.question` | PASS |
| 6 | `answer: current state has separate interview spec and plan nodes` | `workflow.interview.continue` | `workflow.interview.question` | PASS |
| 7 | `answer: risk is losing approval safety or intent compatibility` | `workflow.interview.continue` | `workflow.interview.question` | PASS |
| 8 | `answer: first milestone is parity with existing prompt matrix` | `workflow.interview.continue` | `workflow.interview.ready` | PASS |
| 9 | `complete interview` | `workflow.interview.complete` | `workflow.interview.completed` | PASS |
| 10 | `generate spec` | `workflow.spec.generate` | `workflow.spec.generated` | PASS |
| 11 | `save spec` | `workflow.spec.propose_save` | `approval.request` | PASS (approved + wrote + committed) |
| 12 | `generate plan` | `workflow.plan.generate` | `workflow.plan.generated` | PASS |
| 13 | `save plan` | `workflow.plan.propose_save` | first attempt `error:E_NODE_UNAVAILABLE`, retry `approval.request` | PASS (retry required, then approved + wrote + committed) |

## Approval/Write/Commit Validation
- `save spec` approval flow:
  - `approval.requested` -> `approval.resolved` -> `memory.write.applied` -> `git.committed`
- `save plan` approval flow (retry):
  - `approval.requested` -> `approval.resolved` -> `memory.write.applied` -> `git.committed`

## Artifact Validation
Verified created artifacts:
- `data/library/single-skill-node/interview.md`
- `data/library/single-skill-node/spec.md`
- `data/library/single-skill-node/plan.md`

`data/library/.git` latest commits include:
- `0bf5a3e feat(single-skill-node): approved change`
- `355173a feat(single-skill-node): approved change`

## Router Dispatch Evidence (Unified Node)
`data/runtime/logs/router.jsonl` shows workflow intents dispatched to `node.workflow.skill`:
- `2026-02-26T12:32:18.057654+00:00` -> `workflow.interview.start` -> `node.workflow.skill`
- `2026-02-26T12:32:47.573699+00:00` -> `workflow.spec.generate` -> `node.workflow.skill`
- `2026-02-26T12:33:32.282145+00:00` -> `workflow.plan.generate` -> `node.workflow.skill`
- `2026-02-26T12:34:58.467665+00:00` -> `workflow.plan.propose_save` -> `node.workflow.skill`

## Skill Observability Evidence
`data/runtime/logs/workflow.jsonl` contains unified node events:
- `skill.catalog.loaded`
- `skill.execution.started`
- `skill.execution.completed`
- `skill.execution.failed` (transient OpenRouter unavailability on first `save plan` attempt)

## Conclusion
The single `node.workflow.skill` in BrainDrive-MVP successfully reproduced the interview -> spec -> plan flow with preserved approval/write/commit safety behavior, matching the prior three-node workflow behavior.

Overall status: **PASS**, with one transient model-provider failure recovered by retry.
