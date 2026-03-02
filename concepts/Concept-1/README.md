# Concept-1: Router + Intent Router Docker Compose

This concept container provides a runnable multi-node environment for:

- `node.router` (dynamic structural routing)
- `intent.router.natural-language` (NL intent planning)

It also includes supporting nodes required to test discovery, capability routing, safety gates, and observability.

## Included Nodes

- `node-router` (`node.router`)
- `intent-router-natural-language` (`intent.router.natural-language`)
- `node-auth-policy` (`node.auth.policy`)
- `node-session-state` (`node.session.state`)
- `node-audit-log` (`node.audit.log`)
- `node-activity-feedback` (`node.activity.feedback`)
- `node-chat-general-primary`
- `node-chat-general-backup`
- `node-workflow`

## Why These Support Nodes Exist

They enable meaningful Router/Intent testing against the requirements in `Concepts/Router`:

- Dynamic node registration and heartbeats (all support nodes self-register)
- Capability catalog exploration (`/router/catalog`)
- Deterministic routing and fallback (primary + backup chat nodes)
- Context-aware NL planning (`node.session.state`)
- Policy precheck and fail-closed behavior (`node.auth.policy`)
- Confirmation-gated mutations/destructive actions (`intent-router` + router preflight)
- Decision and activity telemetry (`node.audit.log`, `node.activity.feedback`)

## Ports

- Router API: `http://localhost:9180`
- Intent Router API: `http://localhost:9181`
- Auth Policy control API: `http://localhost:9182`

## Quick Start

```bash
cd /home/hacker/Projects/BrainDrive-Protocal/Concept_Containers/Concept-1
cp .env.example .env
docker compose up -d
```

Check health:

```bash
curl -s http://localhost:9180/health
curl -s http://localhost:9181/health
curl -s http://localhost:9182/health
```

View active node registry:

```bash
curl -s http://localhost:9180/router/registry
```

View capability catalog:

```bash
curl -s http://localhost:9180/router/catalog
```

## Demo Scenarios (Automated)

Run end-to-end scenarios:

```bash
cd /home/hacker/Projects/BrainDrive-Protocal/Concept_Containers/Concept-1
./scripts/demo.sh
```

Validated scenarios:

1. Normal NL chat route to primary chat node
2. Retry/fallback to backup chat node when primary simulates retryable failure
3. Mutation request returns `needs_confirmation` before routing
4. Confirmed mutation routes and executes (`workflow.page.create`)
5. Policy outage (`mode=down`) causes fail-closed denial (`E_POLICY_UNAVAILABLE`)
6. Destructive request (`memory.delete`) requires confirmation and succeeds when confirmed

## Intent Router API

### `POST /intent/plan`

Build plan only (no dispatch).

```json
{
  "message": "Create a new page for Q2 planning",
  "conversation_id": "demo-conv-1"
}
```

### `POST /intent/route`

Build plan and route if safe/confirmed.

```json
{
  "message": "Create a new page for Q2 planning",
  "conversation_id": "demo-conv-1",
  "identity": {
    "actor_id": "user.demo",
    "actor_type": "human",
    "roles": ["admin", "user"]
  },
  "authz": {
    "decision": "allow",
    "decision_id": "demo-authz-1"
  },
  "confirm": true
}
```

## Router API

- `POST /route` (BDP envelope)
- `POST /router/node/register`
- `POST /router/node/heartbeat`
- `GET /router/registry`
- `GET /router/catalog`
- `GET /health`

## Data and Logs

Runtime data persists under:

- `data/router/router-events.jsonl`
- `data/intent/intent-events.jsonl`
- `data/audit/audit-events.jsonl`
- `data/activity/activity-events.jsonl`
- `data/workflow/workflow-events.jsonl`

## Stop

```bash
docker compose down
```
