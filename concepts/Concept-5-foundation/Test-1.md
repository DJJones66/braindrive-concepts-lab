# BrainDrive-MVP Natural Language Test Walkthrough

## 1. Setup

1. Open a terminal:
   - `cd /home/hacker/Projects/BrainDrive-Protocal/BrainDrive-MVP`
2. Confirm `.env` is configured:
   - `BRAINDRIVE_DEFAULT_PROVIDER=openrouter` (or `ollama`)
   - If `openrouter`, set `BRAINDRIVE_OPENROUTER_API_KEY`
3. Start services:
   - `docker compose up -d --build`
4. Start CLI:
   - `python scripts/cli.py`
5. In CLI, enable raw output for intent visibility:
   - `/raw on`

## 2. Prompt Matrix (NL -> Intent -> Node)

| Prompt | Expected Intent | Node |
|---|---|---|
| `list folders` | `folder.list` | `node.workflow.folder` |
| `create folder for my finances` | `folder.create` | `node.workflow.folder` (+ approval flow) |
| `switch folder to my-finances` | `folder.switch` | `node.workflow.folder` |
| `start interview` | `workflow.interview.start` | `node.workflow.skill` |
| `answer: I want to help SMBs with AI automation` | `workflow.interview.continue` | `node.workflow.skill` |
| `complete interview` | `workflow.interview.complete` | `node.workflow.skill` |
| `generate spec` | `workflow.spec.generate` | `node.workflow.skill` |
| `save spec` | `workflow.spec.propose_save` | `node.workflow.skill` -> approval/write/commit flow |
| `generate plan` | `workflow.plan.generate` | `node.workflow.skill` |
| `save plan` | `workflow.plan.propose_save` | `node.workflow.skill` -> approval/write/commit flow |
| `list files` | `memory.list` | `node.memory.fs` |
| `read file about my-finances/spec.md` | `memory.read` | `node.memory.fs` |
| `search files for interview` | `memory.search` | `node.memory.fs` |
| `write file for test note` | `memory.write.propose` | `node.memory.fs` + approval |
| `edit file for test note` | `memory.edit.propose` | `node.memory.fs` + approval |
| `delete file for notes.md` | `memory.delete.propose` | `node.memory.fs` + approval |
| `list models` | `model.catalog.list` | `node.model.openrouter` or `node.model.ollama` |
| `tell me a joke` | `model.chat.complete` (fallback) | active model node |

## 3. How To Verify Routed Node

1. Open a second terminal and run:
   - `tail -f /home/hacker/Projects/BrainDrive-Protocal/BrainDrive-MVP/data/runtime/logs/router.jsonl`
2. For each CLI prompt, check `selected_node_id` in `router.route_dispatched`.

## 4. Model Provider Switching Test

1. Set in `.env`:
   - `BRAINDRIVE_DEFAULT_PROVIDER=openrouter`
2. Restart:
   - `docker compose down && docker compose up -d --build`
3. Run prompt:
   - `tell me a joke`
4. Repeat with:
   - `BRAINDRIVE_DEFAULT_PROVIDER=ollama`
5. Confirm response prefix changes:
   - `[openrouter/<model>] ...` vs `[ollama/<model>] ...`

## 5. Shutdown

- `docker compose down`
