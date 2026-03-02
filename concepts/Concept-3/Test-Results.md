# Concept-3 Prompt Matrix Test Results

Date: 2026-02-25
Environment: docker compose stack in `Concept_Containers/Concept-3` with provider `openrouter`
Execution mode: `python scripts/cli.py` interactive REPL with `/raw on`

## Scope Tested
Source matrix: `Test-1.md` prompt table (18 natural language prompts).

## Pre-checks
- `docker compose up -d --build` completed successfully.
- `docker compose ps` showed all Concept-3 services up.
- Automated suite: `python -m pytest -q` passed.

## Prompt Matrix Results

| # | Prompt | Expected Intent | Actual Intent Path | Result |
|---|---|---|---|---|
| 1 | `list folders` | `folder.list` | `folder.list` -> `folder.listed` | PASS |
| 2 | `create folder for my finances` | `folder.create` (+ approval) | `folder.create` -> `error:E_CONFIRMATION_REQUIRED` -> approved retry -> `folder.created` (`my-finances`) | PASS |
| 3 | `switch folder to my-finances` | `folder.switch` | `folder.switch` -> `folder.switched` (`active_folder=my-finances`) | PASS |
| 4 | `start interview` | `workflow.interview.start` | `workflow.interview.start` -> `workflow.interview.question` (LLM-generated question) | PASS |
| 5 | `answer: I want to help SMBs with AI automation` | `workflow.interview.continue` | `workflow.interview.continue` -> `workflow.interview.question` (`answers_collected=1`) | PASS |
| 6 | `complete interview` | `workflow.interview.complete` | `workflow.interview.complete` -> `workflow.interview.completed` (summary returned) | PASS |
| 7 | `generate spec` | `workflow.spec.generate` | `workflow.spec.generate` -> `workflow.spec.generated` (markdown returned) | PASS |
| 8 | `save spec` | `workflow.spec.propose_save` (+ approval/write/commit) | `workflow.spec.propose_save` -> `approval.request` -> approved -> `memory.write.propose` -> `git.commit.approved_change` | PASS |
| 9 | `generate plan` | `workflow.plan.generate` | `workflow.plan.generate` -> `workflow.plan.generated` (`grounded_in_spec=true`) | PASS |
| 10 | `save plan` | `workflow.plan.propose_save` (+ approval/write/commit) | `workflow.plan.propose_save` -> `approval.request` -> approved -> `memory.write.propose` -> `git.commit.approved_change` | PASS |
| 11 | `list files` | `memory.list` | `memory.list` -> `memory.listed` | PASS |
| 12 | `read file about my-finances/spec.md` | `memory.read` | `memory.read` -> `memory.read.result` (spec content returned) | PASS |
| 13 | `search files for interview` | `memory.search` | `memory.search` -> `memory.search.results` (matches returned) | PASS |
| 14 | `write file for test note` | `memory.write.propose` (+ approval) | `memory.write.propose` -> `error:E_CONFIRMATION_REQUIRED` -> approved retry -> `memory.write.applied` (`notes.md`) | PASS |
| 15 | `edit file for test note` | `memory.edit.propose` (+ approval) | `memory.edit.propose` -> `error:E_CONFIRMATION_REQUIRED` -> approved retry -> `memory.edit.applied` (`notes.md`) | PASS |
| 16 | `delete file for notes.md` | `memory.delete.propose` (+ approval) | `memory.delete.propose` -> `error:E_CONFIRMATION_REQUIRED` -> approved retry -> `memory.delete.applied` (`notes.md`) | PASS |
| 17 | `list models` | `model.catalog.list` | `model.catalog.list` -> `model.catalog` (`provider=openrouter`, model list returned) | PASS |
| 18 | `tell me a joke` | `model.chat.complete` fallback | fallback `model.chat.complete` -> `model.chat.completed` (`provider=openrouter`, model response returned) | PASS |

## Functional Validation Against Specs

### Interview -> Spec -> Plan flow
- Interview used model-driven questioning and produced summary text (not echo behavior).
- Spec generation produced structured markdown including `## Goal`, `## Risks`, `## Open Questions`.
- Plan generation produced phased markdown with `## Phase 1: Clarify`, `## Phase 2: Execute`, `## Phase 3: Validate`.

### Persistence and approvals
- Approved save operations wrote both files:
  - `data/library/my-finances/spec.md`
  - `data/library/my-finances/plan.md`
- Approval records persisted in:
  - `data/runtime/state/approvals.json`
- Workflow state persisted interview summary/spec context in:
  - `data/runtime/state/workflow_state.json`

### Git proof for approved writes
`data/library` git log shows approved change commits:
- `be4d3db feat(my-finances): approved change` -> `my-finances/plan.md`
- `92b6c8c feat(my-finances): approved change` -> `my-finances/spec.md`

### Safety checks
- `notes.md` was created and edited only after explicit confirmations.
- `notes.md` deletion required confirmation and is now absent.

## Router Dispatch Evidence
`data/runtime/logs/router.jsonl` includes dispatch events for every matrix capability and expected node selection, including:
- `node.workflow.folder` for folder intents
- `node.workflow.interview` for interview intents
- `node.workflow.spec` for spec intents
- `node.workflow.plan` for plan intents
- `node.memory.fs` for memory intents
- `node.model.openrouter` for model chat/catalog
- `node.approval.gate` and `node.git.ops` during save flows

## Conclusion
All prompts in the `Test-1.md` prompt matrix were executed and validated end-to-end.

Overall result: **18/18 PASS**.
