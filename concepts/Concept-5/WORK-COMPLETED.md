# Concept-5 Work Completed

Date: 2026-02-26

## Objective
Build `Concept-5` from `BrainDrive-MVP`, implement two new protocol nodes (`ScraplingNode`, `WebConsoleNode`) from design specs, enforce protocol/safety controls with no hardcoded runtime behavior, and deliver comprehensive automated tests.

## Delivery Log

1. Baseline setup
- Copied `BrainDrive-MVP` into `Concept_Containers/Concept-5` as the starting point.
- Kept existing project naming/labels and env naming conventions intact.

2. Scrapling node implementation
- Added `braindrive_runtime/nodes/scrapling.py`.
- Implemented node identity and capability contract:
  - `web.scrape.get`
  - `web.scrape.bulk_get`
  - `web.scrape.fetch`
  - `web.scrape.bulk_fetch`
  - `web.scrape.stealth_fetch`
  - `web.scrape.bulk_stealth_fetch`
- Added policy + safety controls:
  - `http/https` scheme allowlist
  - blocked embedded credentials in URLs
  - domain allowlist/blocklist support
  - private-network/localhost/link-local/metadata endpoint blocking by default
  - DNS resolution guard before request execution
  - bulk URL limit
  - max response content truncation (`max_content_chars`)
  - payload field denylist for script/callback-style inputs
  - stealth mode feature-flag gate
- Added normalized response model:
  - `intent=web.scrape.completed`
  - `payload.mode/results/truncated/limits`
- Added event telemetry:
  - `scrapling.request.started`
  - `scrapling.request.completed`
  - `scrapling.request.failed`
  - `scrapling.request.blocked`
  - `scrapling.policy.denied`
- Added backend adapter behavior:
  - Uses Scrapling MCP server APIs when available
  - Safe fallback HTTP extraction path when Scrapling package is unavailable

3. Web console node implementation
- Added `braindrive_runtime/nodes/web_console.py`.
- Implemented node capability contract:
  - `web.console.session.open`
  - `web.console.session.close`
  - `web.console.targets.list`
  - `web.console.guides.list`
  - `web.console.session.event`
- Enforced protocol/security behavior:
  - required `identity` extension for all web-console capabilities
  - origin allowlist checks
  - per-actor concurrent session limits
  - idle and max session timeout enforcement
  - event size/rate limits
  - role/actor session ownership checks
  - mutate/destructive command approval requirement flow
- Implemented command flow:
  - slash commands (`/help`, `/targets`, `/use`, `/prompts`, `/guide`, `/raw on|off`)
  - terminal event handling (`session.ping`, `terminal.resize`, `terminal.input`, `session.command`, `session.close`)
  - simulated execution fallback and optional routed SSH intent (`WEBTERM_SSH_EXEC_INTENT`)
- Added `.env`-driven SSH auth mode validation and production guard:
  - reject inline private key-only setup outside development
- Added audit/event logs:
  - `data/runtime/logs/webterm_sessions.jsonl`
  - `data/runtime/logs/webterm_events.jsonl`
  - `data/runtime/logs/webterm_security.jsonl`

4. Runtime/service wiring
- Updated exports and registration:
  - `braindrive_runtime/nodes/__init__.py`
  - `braindrive_runtime/runtime.py`
  - `services/node_service.py`
- New runtime node kinds:
  - `scrapling`
  - `web_console`

5. Intent router integration
- Extended `braindrive_runtime/intent_router.py` with NL mappings:
  - scrape prompts -> `web.scrape.*` (including dynamic/stealth/bulk variants)
  - missing scrape URL -> clarification required
  - web console prompts -> `web.console.*`
  - missing web-console origin/session-id -> clarification required
- Preserved metadata-driven confirmation behavior for stealth scraping and protected paths.

6. Compose/env integration
- Added dedicated Scrapling image:
  - `Dockerfile.scrapling`
- Updated compose services:
  - `node-web-scrapling` (dedicated image + Scrapling env surface)
  - `node-web-console`
- Updated `.env.example` with full config surfaces for:
  - Scrapling node
  - Web console node + SSH auth modes/keys

## Test Suite Additions

1. New node tests
- Added `tests/test_scrapling_node.py`:
  - capability metadata contract checks
  - URL/policy validation
  - callback/script field rejection
  - stealth confirmation enforcement
  - truncation enforcement
  - allowlist behavior
- Added `tests/test_web_console_node.py`:
  - metadata contract checks
  - required identity enforcement
  - session open/close/targets/guides
  - origin denial
  - per-user session limit
  - slash command behavior
  - approval-required mutate command flow
  - session expiration handling
  - production inline-key security validation

2. Existing router tests expanded
- Updated `tests/test_router_behavior.py`:
  - scrape prompt NL analysis coverage (get/fetch/stealth/bulk)
  - scrape clarification path when URL is missing
  - web-console analysis coverage and clarification flow

## Validation Run

Executed in:

```bash
cd /home/hacker/Projects/BrainDrive-Protocal/Concept_Containers/Concept-5
python -m pytest
```

Result:
- **74 passed in 2.97s**

## Files Added

- `braindrive_runtime/nodes/scrapling.py`
- `braindrive_runtime/nodes/web_console.py`
- `Dockerfile.scrapling`
- `tests/test_scrapling_node.py`
- `tests/test_web_console_node.py`

## Files Updated

- `braindrive_runtime/nodes/__init__.py`
- `braindrive_runtime/runtime.py`
- `braindrive_runtime/intent_router.py`
- `services/node_service.py`
- `docker-compose.yml`
- `.env.example`
- `tests/test_router_behavior.py`

---

Date: 2026-02-27

## Additional Delivery Log (Dev Web Terminal)

Objective:
- Add a separate dev-only browser terminal with authentication that starts in the BrainDrive natural-language CLI flow (matching terminal behavior) while still allowing raw container shell access.

Completed:

1. Added a dedicated image for the dev web terminal
- Added `Dockerfile.webterm`.
- Includes:
  - Python runtime for `scripts/cli.py`
  - `bash`, `git`, `curl`
  - `ttyd` web terminal binary (`v1.7.7`)

2. Added authenticated terminal entry script
- Added `scripts/dev_webterm_entry.sh`.
- Enforces required auth envs (`DEV_WEBTERM_AUTH_USER`, `DEV_WEBTERM_AUTH_PASSWORD`).
- Starts session in BrainDrive CLI:
  - `python -u scripts/cli.py`
- On CLI exit, automatically drops to raw interactive shell.
- Prints re-entry instruction for CLI from raw shell.

3. Added dev-only compose service
- Updated `docker-compose.yml` with `dev-web-terminal` service.
- Service characteristics:
  - `profiles: ["dev"]` (not part of default startup)
  - Port mapping:
    - host `BRAINDRIVE_DEV_WEBTERM_PORT` (default `9494`)
    - container `7681`
  - Basic auth wired from env (`DEV_WEBTERM_AUTH_USER` / `DEV_WEBTERM_AUTH_PASSWORD`)
  - CLI connectivity envs wired for container network:
    - `BRAINDRIVE_ROUTER_BASE=http://node-router:8080`
    - `BRAINDRIVE_INTENT_BASE=http://intent-router-natural-language:8081`

4. Added configuration surface
- Updated `.env.example`:
  - `BRAINDRIVE_DEV_WEBTERM_PORT=9494`
  - `DEV_WEBTERM_AUTH_USER=dev`
  - `DEV_WEBTERM_AUTH_PASSWORD=change-me-now`

5. Updated operational documentation
- Updated `README.md`:
  - topology and ports now include dev web terminal
  - added run instructions for `--profile dev`
  - added auth and `.env` guidance
  - documented CLI-first then raw-shell behavior

6. Resolved remote-IP auth issue and hardened defaults
- Root cause: running `dev-web-terminal` container still had stale env credentials from initial startup.
- Verified remote requests from `10.1.2.x` were reaching terminal service; issue was not origin/IP rejection.
- Applied fix:
  - recreate service after `.env` change (`docker compose --profile dev up -d --force-recreate dev-web-terminal`)
  - validated auth works on `http://10.1.2.149:9494` with configured creds.
- Hardened logging:
  - added `DEV_WEBTERM_LOG_LEVEL` (default `2`) to reduce ttyd startup disclosure in logs.
- Added exposure control:
  - `BRAINDRIVE_DEV_WEBTERM_BIND_ADDR` to bind terminal port to a specific interface/IP.

---

Date: 2026-02-27

## Additional Delivery Log (Scrapling Default Save Directory)

Objective:
- Store scrape output in library by default and auto-create directory when missing.

Completed:

1. Implemented default scrape persistence in `node.web.scrapling`
- Updated `braindrive_runtime/nodes/scrapling.py`.
- Added env-driven defaults:
  - `BRAINDRIVE_SCRAPLING_DEFAULT_SAVE` (default `true`)
  - `BRAINDRIVE_SCRAPLING_DEFAULT_SAVE_DIR` (default `scraping`)
- Added safe relative path handling for `save_directory`.
- Auto-creates target directory under library root on first save.
- Saves one file per scrape result with deterministic timestamped filenames.
- Added response metadata under `payload.storage`:
  - `saved`
  - `directory`
  - `files[]` (`path`, `url`, `bytes`)
- Added per-request payload controls:
  - `save_to_library` (bool)
  - `save_directory` (relative subdirectory)

2. Added test coverage
- Updated `tests/test_scrapling_node.py`:
  - validates default save creates `library/scraping`
  - validates saved file exists with expected content
  - validates per-request opt-out (`save_to_library=false`)

3. Updated deployment and docs
- Updated `docker-compose.yml` scrapling env wiring.
- Updated `.env.example` with new scrapling save env vars.
- Updated `README.md` with persistence behavior and overrides.

---

Date: 2026-02-27

## Additional Delivery Log (Interview Prompt Hardening)

Objective:
- Ensure interview flow remains software/workspace-focused and does not drift into physical-folder interpretation.

Findings:
- Interview routing was functioning (start/continue/complete intents were correct).
- The issue was prompt ambiguity: model-generated question text could interpret the word `folder` semantically as physical object.

Completed:

1. Hardened interview prompt construction in SkillWorkflowNode
- Updated `braindrive_runtime/nodes/skill.py`:
  - `_build_next_question_prompt` now explicitly states:
    - folder means digital project workspace in BrainDrive
    - do not ask physical-vs-digital questions
    - focus on software behavior, user flow, constraints, and outcomes
  - Injected workspace `AGENT.md` context into question-generation prompt.
  - Added `_normalize_interview_question` guard to replace any physical-folder style question with a safe software-outcome question.
  - Applied normalization in both interview start and continue paths.

2. Updated default bootstrap skill templates
- Updated `braindrive_runtime/nodes/runtime_bootstrap.py` interview prompt templates (`start/continue/complete`) with digital-workspace language.

3. Updated live skill prompt files used by current runtime
- Updated:
  - `data/library/.braindrive/skills/interview/prompts/start.md`
  - `data/library/.braindrive/skills/interview/prompts/continue.md`
  - `data/library/.braindrive/skills/interview/prompts/complete.md`
  - `data/library/.braindrive/skills/interview.md` (legacy compatibility)

4. Added automated tests
- Updated `tests/test_skill_node.py`:
  - verifies interview prompt includes digital-workspace constraints
  - verifies physical-folder phrasing is normalized to a software-outcome question

5. Runtime validation
- Restarted `node-workflow-skill`.
- Verified live route sequence:
  - `workflow.interview.start` question is software/workspace-goal oriented
  - `workflow.interview.continue` with answer `"The scraping folder"` generates software/data-source follow-up question (no physical-folder wording)

---

Date: 2026-02-27

## Additional Delivery Log (Centralized Timeout Controls)

Objective:
- Expose timeout controls in `.env` for easy tuning and increase headroom to prevent interview answer timeout failures.

Completed:

1. Added/updated timeout values in `Concept_Containers/Concept-5/.env`
- `BRAINDRIVE_CLI_TIMEOUT_SEC=300`
- `INTENT_ROUTER_CATALOG_TIMEOUT_SEC=5`
- `INTENT_ROUTER_ROUTE_TIMEOUT_SEC=240`
- `ROUTER_NODE_TIMEOUT_SEC=180`
- `ROUTER_DIRECT_ROUTE_TIMEOUT_SEC=180`
- `BRAINDRIVE_MODEL_TIMEOUT_SEC=180`
- `WEBTERM_HTTP_TIMEOUT_SEC=60`

2. Mirrored timeout controls in `.env.example`
- Added `BRAINDRIVE_CLI_TIMEOUT_SEC` and `ROUTER_DIRECT_ROUTE_TIMEOUT_SEC`.
- Raised matching defaults for route/model/web console timeouts.

3. Wired compose services to consume env timeout controls
- Updated `docker-compose.yml` to pass:
  - `ROUTER_DIRECT_ROUTE_TIMEOUT_SEC` into all `node_service.py` node containers.
  - `BRAINDRIVE_CLI_TIMEOUT_SEC` into `dev-web-terminal`.
- Updated compose fallback defaults to match new timeout profile.

4. Applied runtime changes
- Recreated all core services and `dev-web-terminal` with updated env.
- Verified active container env values now reflect expanded timeouts.

5. Live validation
- Executed interview start + continue via `intent/route`.
- Result:
  - start returned `workflow.interview.question`
  - continue returned `workflow.interview.question`
  - no timeout/error on answer submission

---

Date: 2026-02-27

## Additional Delivery Log (Dev Web Terminal Line-Wrap Fix)

Objective:
- Fix long-input wrapping behavior in the dev web terminal where text appeared to overwrite/start in the wrong column.

Root cause:
- Colored prompt ANSI sequences were passed directly to `input()`.
- In ttyd/readline sessions, readline miscounted visible prompt width, causing cursor/line-wrap glitches during long input.

Completed:

1. Readline-safe prompt handling in CLI
- Updated `scripts/cli.py`:
  - Added ANSI escape detection regex for prompt handling.
  - Added `_READLINE_ACTIVE` tracking in `_setup_line_editing()`.
  - Added `_readline_safe_prompt()` to wrap non-printing ANSI bytes with readline markers (`\\001` / `\\002`).
  - Updated REPL input call to use `input(_readline_safe_prompt(client.prompt()))`.

2. Validation
- Ran full test suite: `python -m pytest -q` (passed).
- Recreated `dev-web-terminal` container to load updated CLI startup behavior.

---

Date: 2026-02-27

## Additional Delivery Log (Interview Answer Misroute Fix)

Objective:
- Stop interview answers from being misinterpreted as `folder.switch` when natural text contains phrase fragments like `work on`.

Issue observed:
- During interview mode (`awaiting_answer=true`), answer text such as:
  - `I own 2 of these and want to work on projects...`
- Was classified as `folder.switch` due to broad keyword matching (`work on`) and resulted in:
  - `E_NODE_ERROR: Folder not found: ...`

Root cause:
- In `intent_router`, folder-switch keyword matching was evaluated before context-aware interview continuation fallback.
- The `folder.switch` heuristic accepted plain `work on` without requiring explicit folder wording.

Completed:

1. Intent routing precedence fix
- Updated `braindrive_runtime/intent_router.py`:
  - Added early context guard:
    - when `awaiting_answer=true`, route plain text to `workflow.interview.continue`
    - exception: explicit `complete interview` / `finish interview` still routes to completion

2. Folder-switch heuristic tightening
- Updated keyword/regex matching:
  - from broad `work on`
  - to explicit `work on folder`
- Prevents accidental folder intent capture from normal conversational answers.

3. Regression tests
- Updated `tests/test_router_behavior.py`:
  - added test ensuring interview context overrides `work on ...` phrase
  - added test ensuring `complete interview` still routes correctly while awaiting answer

4. Validation
- Ran targeted + full test suite:
  - `tests/test_router_behavior.py` passed
  - full `pytest` suite passed
- Recreated `intent-router-natural-language` container.
- Live route check confirmed:
  - `start interview` -> `workflow.interview.question`
  - answer containing `work on projects` -> `workflow.interview.continue` and no folder error
