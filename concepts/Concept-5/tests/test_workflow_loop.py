from __future__ import annotations

from pathlib import Path

from braindrive_runtime.runtime import BrainDriveRuntime


def _msg(intent, payload, extensions=None):
    from braindrive_runtime.protocol import new_uuid

    body = {
        "protocol_version": "0.1",
        "message_id": new_uuid(),
        "intent": intent,
        "payload": payload,
    }
    if extensions is not None:
        body["extensions"] = extensions
    return body


def _create_and_switch(runtime):
    runtime.route(
        _msg(
            "folder.create",
            {"topic": "Finances"},
            {"confirmation": {"required": True, "status": "approved", "request_id": "appr-folder"}},
        )
    )
    runtime.route(_msg("folder.switch", {"folder": "finances"}))


def _complete_interview(runtime):
    runtime.route(_msg("workflow.interview.start", {}))
    for answer in [
        "Grow emergency savings",
        "Maintain 6-month buffer",
        "Current savings are limited",
        "Income volatility",
        "Automate weekly transfers",
    ]:
        runtime.route(_msg("workflow.interview.continue", {"answer": answer}))
    runtime.route(_msg("workflow.interview.complete", {}))


def test_folder_to_interview_to_spec_to_plan(runtime, make_message):
    _create_and_switch(runtime)
    _complete_interview(runtime)

    spec_proposal = runtime.route(make_message("workflow.spec.propose_save", {}))
    assert spec_proposal["intent"] == "approval.request"
    spec_flow = runtime.apply_approval_flow(spec_proposal["payload"], approve=True)
    assert spec_flow["write"]["intent"] == "memory.write.applied"

    plan_proposal = runtime.route(make_message("workflow.plan.propose_save", {}))
    assert plan_proposal["intent"] == "approval.request"
    plan_flow = runtime.apply_approval_flow(plan_proposal["payload"], approve=True)
    assert plan_flow["write"]["intent"] == "memory.write.applied"

    assert (runtime.library_root / "finances" / "spec.md").exists()
    assert (runtime.library_root / "finances" / "plan.md").exists()

    next_steps = runtime.route(make_message("chat.general", {"text": "what next?"}))
    assert next_steps["intent"] == "chat.response"
    assert next_steps["payload"].get("source") == "finances/plan.md"
    assert len(next_steps["payload"].get("next_steps", [])) >= 1

    interview_path = runtime.library_root / "finances" / "interview.md"
    assert interview_path.exists()
    interview_text = interview_path.read_text(encoding="utf-8")
    assert "## Interview Session " in interview_text
    assert "- Started: `" in interview_text
    assert "- Completed: `" in interview_text


def test_mid_interview_restart_preserves_state(tmp_path: Path):
    env = {
        "BRAINDRIVE_DEFAULT_PROVIDER": "openrouter",
        "BRAINDRIVE_OPENROUTER_API_KEY": "test-key",
        "BRAINDRIVE_OPENROUTER_DEFAULT_MODEL": "anthropic/claude-sonnet-4",
        "BRAINDRIVE_OLLAMA_BASE_URL": "http://localhost:11434/v1",
        "BRAINDRIVE_OLLAMA_DEFAULT_MODEL": "llama3:8b",
    }
    library = tmp_path / "library"
    data = tmp_path / "runtime-data"

    first = BrainDriveRuntime(library_root=library, data_root=data, env=env)
    first.bootstrap()
    _create_and_switch(first)
    first.route(_msg("workflow.interview.start", {}))
    first.route(_msg("workflow.interview.continue", {"answer": "First answer"}))

    second = BrainDriveRuntime(library_root=library, data_root=data, env=env)
    second.bootstrap()
    second.route(_msg("folder.switch", {"folder": "finances"}))

    resumed = second.route(_msg("workflow.interview.continue", {"answer": "Second answer"}))
    assert resumed["intent"] in {"workflow.interview.question", "workflow.interview.ready"}


def test_what_next_uses_plan_context(runtime, make_message):
    _create_and_switch(runtime)
    plan_path = runtime.library_root / "finances" / "plan.md"
    plan_path.write_text(
        "# Finances Plan\n\n- Review spending baseline\n- Transfer to savings every Friday\n- Revisit in 30 days\n",
        encoding="utf-8",
    )

    response = runtime.route(make_message("chat.general", {"text": "what next should I do"}))
    assert response["intent"] == "chat.response"
    assert response["payload"]["source"] == "finances/plan.md"
    assert "Review spending baseline" in " ".join(response["payload"]["next_steps"])
