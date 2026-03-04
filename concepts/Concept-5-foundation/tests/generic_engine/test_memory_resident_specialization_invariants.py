from __future__ import annotations

from pathlib import Path

from braindrive_runtime.protocol import new_uuid
from braindrive_runtime.runtime import BrainDriveRuntime


CUSTOM_READY_INTENT = "workflow.interview.ready.custom"


def _msg(intent: str, payload: dict, extensions: dict | None = None) -> dict:
    body = {
        "protocol_version": "0.1",
        "message_id": new_uuid(),
        "intent": intent,
        "payload": payload,
    }
    if extensions is not None:
        body["extensions"] = extensions
    return body


def _runtime(tmp_path: Path) -> BrainDriveRuntime:
    library = tmp_path / "library"
    skills_dir = library / ".braindrive" / "skills" / "interview"
    prompts_dir = skills_dir / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)

    (skills_dir / "skill.yaml").write_text(
        f"""
skill_id: interview
version: 1.0.0
actions:
  start:
    execution_tier: stateful
    operation: session.start
    question_intent: workflow.interview.question
    prompt_template: prompts/start.md
  continue:
    execution_tier: stateful
    operation: session.step
    question_intent: workflow.interview.question
    ready_intent: {CUSTOM_READY_INTENT}
    next_intent: workflow.interview.complete
    prompt_template: prompts/continue.md
  complete:
    execution_tier: stateful
    operation: session.complete
    completed_intent: workflow.interview.completed
    prompt_template: prompts/complete.md
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (prompts_dir / "start.md").write_text("# Interview Start\n", encoding="utf-8")
    (prompts_dir / "continue.md").write_text("# Interview Continue\n", encoding="utf-8")
    (prompts_dir / "complete.md").write_text("# Interview Complete\n", encoding="utf-8")

    rt = BrainDriveRuntime(
        library_root=library,
        data_root=tmp_path / "runtime-data",
        env={
            "BRAINDRIVE_DEFAULT_PROVIDER": "openrouter",
            "BRAINDRIVE_OPENROUTER_API_KEY": "test-key",
            "BRAINDRIVE_OPENROUTER_DEFAULT_MODEL": "anthropic/claude-sonnet-4",
            "BRAINDRIVE_OLLAMA_BASE_URL": "http://localhost:11434/v1",
            "BRAINDRIVE_OLLAMA_DEFAULT_MODEL": "llama3:8b",
            "BRAINDRIVE_ENABLE_TEST_ENDPOINTS": "true",
        },
    )
    rt.bootstrap()
    return rt


def test_interview_ready_intent_is_manifest_driven(tmp_path: Path):
    runtime = _runtime(tmp_path)

    created = runtime.route(
        _msg(
            "folder.create",
            {"topic": "Invariant"},
            {"confirmation": {"required": True, "status": "approved", "request_id": "appr-invariant"}},
        )
    )
    assert created["intent"] == "folder.created"
    switched = runtime.route(_msg("folder.switch", {"folder": "invariant"}))
    assert switched["intent"] == "folder.switched"

    started = runtime.route(
        _msg(
            "skill.execute.stateful",
            {
                "skill_id": "interview",
                "action": "start",
                "context": {"folder": "invariant"},
            },
        )
    )
    assert started["intent"] == "skill.executed"

    last = None
    for answer in ["one", "two", "three", "four", "five"]:
        last = runtime.route(
            _msg(
                "skill.execute.stateful",
                {
                    "skill_id": "interview",
                    "action": "continue",
                    "context": {"folder": "invariant"},
                    "answer": answer,
                },
            )
        )

    assert isinstance(last, dict)
    assert last["intent"] == "skill.executed"
    result = last["payload"].get("result", {})
    assert result.get("intent") == CUSTOM_READY_INTENT
