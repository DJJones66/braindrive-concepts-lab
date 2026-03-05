from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from braindrive_runtime.protocol import new_uuid
from braindrive_runtime.runtime import BrainDriveRuntime


def _msg(intent: str, payload: Dict[str, Any], extensions: Dict[str, Any] | None = None) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "protocol_version": "0.1",
        "message_id": new_uuid(),
        "intent": intent,
        "payload": payload,
    }
    if extensions is not None:
        body["extensions"] = extensions
    return body


def _approved_extensions() -> Dict[str, Any]:
    return {
        "confirmation": {
            "required": True,
            "status": "approved",
            "request_id": f"test-{new_uuid()}",
        }
    }


def _write_workflow_config(library_root: Path, payload: Dict[str, Any]) -> None:
    target = library_root / ".braindrive" / "system" / "workflow-config.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")


def _runtime_with_workflow_config(tmp_path: Path, workflow_payload: Dict[str, Any]) -> BrainDriveRuntime:
    library = tmp_path / "library"
    data = tmp_path / "runtime-data"
    library.mkdir(parents=True, exist_ok=True)
    _write_workflow_config(library, workflow_payload)

    runtime = BrainDriveRuntime(
        library_root=library,
        data_root=data,
        env={
            "BRAINDRIVE_DEFAULT_PROVIDER": "openrouter",
            "BRAINDRIVE_OPENROUTER_API_KEY": "test-key",
            "BRAINDRIVE_OPENROUTER_DEFAULT_MODEL": "anthropic/claude-sonnet-4",
            "BRAINDRIVE_OLLAMA_BASE_URL": "http://localhost:11434/v1",
            "BRAINDRIVE_OLLAMA_DEFAULT_MODEL": "llama3:8b",
            "BRAINDRIVE_ENABLE_TEST_ENDPOINTS": "true",
        },
    )
    runtime.bootstrap()
    return runtime


def test_workflow_alias_and_notes_path_can_be_reconfigured(tmp_path: Path) -> None:
    runtime = _runtime_with_workflow_config(
        tmp_path,
        {
            "paths": {"notes": "scratchpad.md"},
            "intent_aliases": {"workflow.spec.generate": ["draft blueprint"]},
        },
    )

    alias_match = runtime.analyze("please draft blueprint for this folder")
    assert alias_match["canonical_intent"] == "workflow.spec.generate"

    write_match = runtime.analyze("write file with these notes")
    assert write_match["canonical_intent"] == "memory.write.propose"
    assert write_match["payload"]["path"] == "scratchpad.md"


def test_workflow_artifact_and_context_paths_can_be_reconfigured(tmp_path: Path) -> None:
    runtime = _runtime_with_workflow_config(
        tmp_path,
        {
            "paths": {
                "agent": "CONTEXT.md",
                "spec": "blueprint.md",
                "plan": "tasks.md",
                "interview": "intake.md",
            },
            "context_docs": ["CONTEXT.md", "blueprint.md", "tasks.md"],
        },
    )

    created = runtime.route_nl("create folder alpha", confirm=True)
    assert created["status"] == "routed"
    assert created["route_response"]["payload"]["agent_path"] == "alpha/CONTEXT.md"

    saved_plan = runtime.route(
        _msg(
            "memory.write.propose",
            {"path": "alpha/tasks.md", "content": "- first step\n- second step\n"},
            _approved_extensions(),
        )
    )
    assert saved_plan["intent"] == "memory.write.applied"

    current = runtime.route(_msg("folder.current.get", {}))
    assert current["intent"] == "folder.current"
    docs = current["payload"]["context_docs"]
    assert "CONTEXT.md" in docs
    assert "tasks.md" in docs

    next_steps = runtime.route(_msg("chat.general", {"text": "what next?"}))
    assert next_steps["intent"] == "chat.response"
    assert next_steps["payload"]["source"] == "alpha/tasks.md"

    spec_propose = runtime.route(_msg("workflow.spec.propose_save", {"spec_markdown": "# Spec"}))
    assert spec_propose["intent"] == "approval.request"
    assert spec_propose["payload"]["proposed_write"]["path"] == "alpha/blueprint.md"

    plan_propose = runtime.route(_msg("workflow.plan.propose_save", {"plan_markdown": "# Plan"}))
    assert plan_propose["intent"] == "approval.request"
    assert plan_propose["payload"]["proposed_write"]["path"] == "alpha/tasks.md"


def test_bootstrap_seeds_workflow_config_when_missing(tmp_path: Path) -> None:
    library = tmp_path / "library"
    data = tmp_path / "runtime-data"
    runtime = BrainDriveRuntime(
        library_root=library,
        data_root=data,
        env={
            "BRAINDRIVE_DEFAULT_PROVIDER": "openrouter",
            "BRAINDRIVE_OPENROUTER_API_KEY": "test-key",
            "BRAINDRIVE_OPENROUTER_DEFAULT_MODEL": "anthropic/claude-sonnet-4",
            "BRAINDRIVE_OLLAMA_BASE_URL": "http://localhost:11434/v1",
            "BRAINDRIVE_OLLAMA_DEFAULT_MODEL": "llama3:8b",
        },
    )

    boot = runtime.bootstrap()
    assert boot["bootstrap"]["intent"] == "system.bootstrap.ready"

    config_path = library / ".braindrive" / "system" / "workflow-config.json"
    assert config_path.exists()
