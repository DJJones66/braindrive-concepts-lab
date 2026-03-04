# BrainDrive-MVP No-Prefix Interview Test Results

Date: 2026-02-26
Environment: docker compose stack in `BrainDrive-MVP` (router `9480`, intent router `9481`)
Execution mode: scripted `/intent/route` calls using interview-awaiting context (same behavior used by CLI when waiting for an interview reply)

## Test Goal
Verify that interview answers work **without** `answer:` prefix.

## Important Input Constraint Used
For plain-text interview replies, calls were sent with:
- `context.interview.awaiting_answer=true`

This matches the intended “no prefix needed while interview is active” flow.

## Prompt Sequence Results

| # | Prompt | Used `answer:` | Canonical Intent | Route Intent | Result |
|---|---|---|---|---|---|
| 1 | `create folder for no-prefix-interview-test` | No | `folder.create` | `folder.created` | PASS |
| 2 | `switch folder to no-prefix-interview-test` | No | `folder.switch` | `folder.switched` | PASS |
| 3 | `start interview` | No | `workflow.interview.start` | `workflow.interview.question` | PASS |
| 4 | `I am replacing three workflow nodes with one skill node` | No | `workflow.interview.continue` | `workflow.interview.question` | PASS |
| 5 | `Success means interview spec and plan flows still work unchanged` | No | `workflow.interview.continue` | `workflow.interview.question` | PASS |
| 6 | `The current system already has approval gates and routing logs` | No | `workflow.interview.continue` | `workflow.interview.question` | PASS |
| 7 | `Main risk is losing intent compatibility during migration` | No | `workflow.interview.continue` | `workflow.interview.question` | PASS |
| 8 | `First milestone is passing the same workflow test matrix` | No | `workflow.interview.continue` | `workflow.interview.ready` | PASS |
| 9 | `complete interview` | No | `workflow.interview.complete` | `workflow.interview.completed` | PASS |
| 10 | `generate spec` | No | `workflow.spec.generate` | `workflow.spec.generated` | PASS |
| 11 | `save spec` | No | `workflow.spec.propose_save` | `approval.request` | PASS |
| 12 | `generate plan` | No | `workflow.plan.generate` | `error` | RETRY NEEDED |
| 13 | `save plan` | No | `workflow.plan.propose_save` | `approval.request` | PASS |
| 14 | `generate plan` (retry) | No | `workflow.plan.generate` | `workflow.plan.generated` | PASS |

## Approval Flow Checks
Both save actions completed full approval/write/commit flow:
- `save spec`: `approval.requested` -> `approval.resolved` -> `memory.write.applied` -> `git.committed`
- `save plan`: `approval.requested` -> `approval.resolved` -> `memory.write.applied` -> `git.committed`

## Router Dispatch Evidence
Recent `router.route_dispatched` events show all workflow intents selecting `node.workflow.skill`, including interview continuations from plain answers.

## Artifact Checks
Verified files created:
- `data/library/no-prefix-interview-test/interview.md`
- `data/library/no-prefix-interview-test/spec.md`
- `data/library/no-prefix-interview-test/plan.md`

Git history in `data/library` includes approved commits:
- `c0ee15e feat(no-prefix-interview-test): approved change`
- `34bff97 feat(no-prefix-interview-test): approved change`

## Conclusion
Yes, plain interview replies **without `answer:`** are working in BrainDrive-MVP when interview-awaiting context is active.

Overall: **PASS** (with one transient provider error on the first `generate plan` attempt, resolved on retry).

## Addendum: Fallback Chat Streaming Test (2026-02-26)

Goal:
- Verify fallback model chat responses can stream real tokens in CLI.

Configuration:
- `BRAINDRIVE_CLI_STREAM_MODEL_CHAT=true`
- `BRAINDRIVE_CLI_STREAM_FALLBACK_ONLY=true`

Checks:
1. `python scripts/cli.py --skip-bootstrap --message "streaming check hello"`
   - Result: `model.chat` response returned successfully via CLI streaming path (`[provider/model] ...` output).
2. `python scripts/cli.py --skip-bootstrap --message "list folders"`
   - Result: non-chat workflow output unchanged (`folder.list` behavior preserved).
3. `BRAINDRIVE_CLI_STREAM_MODEL_CHAT=false python scripts/cli.py --skip-bootstrap --message "hello no stream"`
   - Result: fallback chat still works via non-stream route (stream toggle confirmed).

Conclusion:
- Real-time fallback chat streaming is enabled and controlled via `.env`.
- Non-chat intents continue to use normal routing behavior.

## Addendum 2: Direct Provider Streaming Verification (2026-02-26)

Objective:
- Prove responses are real streamed SSE chunks from provider (not simulated typing).

Method:
- Sent direct OpenRouter `/chat/completions` requests with `stream=true`.
- Collected:
  - `ttft` (time to first non-empty chunk)
  - chunk count
  - total streamed chars
  - total completion time
- Compared against `stream=false` completion timing.

Observed (active default model):
- Provider/model: `openrouter / minimax/minimax-m2.5`
- Long prompt probe:
  - `ttft`: `24.449s`
  - stream total: `33.156s`
  - chunks: `544`
  - chars: `2619`
  - avg chunk size: `4.81` chars
- Same prompt with `stream=false`:
  - total: `64.93s`

Model comparison probe:
- `minimax/minimax-m2.5`: `ttft=30.644s`, `total=33.086s`, `chunks=153`
- `anthropic/claude-sonnet-4`: `ttft=1.055s`, `total=4.821s`, `chunks=66`

Interpretation:
- Streaming is real (many incremental chunks before completion).
- “Late start” is model/provider TTFT behavior, not fake streaming.
- Chunk granularity is determined by provider/model emission cadence and may be word/phrase-sized, not per-character.
