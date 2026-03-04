# Test-Needed

Date: 2026-02-26
Directory: `/home/hacker/Projects/BrainDrive-Protocal/Concept_Containers/Concept-5`

This file lists what you need to do or provide so I can continue deeper validation for existing nodes and the 2 new nodes.

## 1. Global Requirements (Needed First)

1. Confirm test mode:
- `local pytest only`
- `docker integration`
- `full e2e (recommended)`

2. Provide provider/access setup:
- `BRAINDRIVE_OPENROUTER_API_KEY` (if testing OpenRouter)
- Running Ollama endpoint + model pulled (if testing Ollama)

3. Confirm legal/policy test scope for web scraping:
- Allowed domains for real tests
- Whether stealth scraping tests are approved in your environment

4. Confirm web-terminal security context:
- Real allowed browser origins
- Identity/JWT source used for `extensions.identity`

## 2. What To Run/Share (Core Artifacts)

1. Service status:
```bash
cd /home/hacker/Projects/BrainDrive-Protocal/Concept_Containers/Concept-5
docker compose ps
```

2. Health checks:
```bash
curl -s http://localhost:9480/health | jq
curl -s http://localhost:9481/health | jq
```

3. Full tests:
```bash
python -m pytest
```

4. Share outputs/logs:
- `docker compose ps`
- pytest result summary
- any failing traceback
- `data/runtime/logs/*.jsonl` snippets for failed flows

## 3. Existing Nodes: Needed Inputs/Scenarios

1. `runtime_bootstrap`
- Run bootstrap once and confirm `.braindrive` scaffold creation.

2. `memory_fs`
- Provide sample read/write/edit/delete scenarios you care about.
- Confirm approval behavior expectations for mutate/destructive paths.

3. `folder`
- Provide folder naming edge-cases to validate (spaces, symbols, casing).

4. `skill`
- Provide one full interview/spec/plan topic for quality validation.
- Confirm acceptable output quality criteria.

5. `approval_gate`
- Confirm who is allowed to approve/deny in your workflow.

6. `git_ops`
- Confirm if plain local commits are acceptable or if signing/conventions are required.

7. `model_openrouter` / `model_ollama`
- Provide expected default model(s).
- Confirm timeout expectations.

8. `chat_general`
- Provide examples where fallback chat should/should not be used.

9. `audit_log`
- Confirm retention/privacy expectations for runtime logs.

## 4. New Node: ScraplingNode (Needed For Deeper Validation)

1. Provide real test URLs by type:
- static HTML URL(s)
- dynamic JS-rendered URL(s)
- stealth-required URL(s) (only if approved)

2. Confirm policy values:
- `BRAINDRIVE_SCRAPLING_ALLOWED_DOMAINS`
- `BRAINDRIVE_SCRAPLING_BLOCKED_DOMAINS`
- `BRAINDRIVE_SCRAPLING_ALLOW_PRIVATE_NET` (usually `false`)

3. Confirm acceptance checks:
- expected extraction type per URL (`markdown/html/text`)
- expected key content markers
- max acceptable latency/timeouts

4. If testing Dockerized Scrapling fully:
- run `docker compose up -d --build`
- share `node-web-scrapling` logs if any failures

## 5. New Node: WebConsoleNode (Needed For Deeper Validation)

1. Confirm auth/identity model:
- how browser/session auth maps to `extensions.identity`
- expected roles for terminal access

2. Confirm origin and target policy:
- final `WEBTERM_ALLOWED_ORIGINS`
- final `WEBTERM_TARGETS`

3. Confirm SSH mode and secrets source:
- `WEBTERM_SSH_AUTH_MODE`
- key/cert file paths (preferred) or dev-only inline values

4. Provide command policy expectations:
- allowed read commands
- mutate commands requiring approval
- destructive commands policy

5. Validate session controls:
- idle timeout target
- max session lifetime target
- per-user concurrent session limit

6. For integration test:
- run session open -> command -> approval-required command -> approved retry -> close
- share `webterm_sessions.jsonl`, `webterm_events.jsonl`, `webterm_security.jsonl`

## 6. Optional But Useful

1. Provide a priority order for deeper testing:
- `security`
- `e2e reliability`
- `performance/latency`
- `policy hardening`

2. Provide pass/fail gates for sign-off:
- must-pass node flows
- acceptable known limitations for this concept stage
