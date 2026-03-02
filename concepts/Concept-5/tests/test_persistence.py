from __future__ import annotations

from pathlib import Path

from braindrive_runtime.protocol import new_uuid
from braindrive_runtime.runtime import BrainDriveRuntime


def _msg(intent, payload, extensions=None):
    body = {
        "protocol_version": "0.1",
        "message_id": new_uuid(),
        "intent": intent,
        "payload": payload,
    }
    if extensions is not None:
        body["extensions"] = extensions
    return body


def _runtime(library: Path, data: Path) -> BrainDriveRuntime:
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


def test_restart_preserves_logs_and_state(tmp_path: Path):
    library = tmp_path / "library"
    data = tmp_path / "runtime-data"

    first = _runtime(library, data)
    first.route(_msg("chat.general", {"text": "hello"}))
    first.route(_msg("system.health.check", {}))

    router_log = data / "logs" / "router.jsonl"
    before = router_log.read_text(encoding="utf-8")
    assert before.strip()

    second = _runtime(library, data)
    second.route(_msg("chat.general", {"text": "hello again"}))

    after = router_log.read_text(encoding="utf-8")
    assert len(after.splitlines()) >= len(before.splitlines())


def test_restart_during_workflow_preserves_interview_state(tmp_path: Path):
    library = tmp_path / "library"
    data = tmp_path / "runtime-data"

    first = _runtime(library, data)
    first.route(
        _msg(
            "folder.create",
            {"topic": "Finances"},
            {"confirmation": {"required": True, "status": "approved", "request_id": "appr-folder"}},
        )
    )
    first.route(_msg("folder.switch", {"folder": "finances"}))
    first.route(_msg("workflow.interview.start", {}))
    first.route(_msg("workflow.interview.continue", {"answer": "Initial answer"}))

    second = _runtime(library, data)
    state = second.workflow_state.get()
    interviews = state.get("interviews", {})
    assert "finances" in interviews
    assert len(interviews["finances"].get("answers", [])) == 1


def test_multiple_interviews_append_history_document(tmp_path: Path):
    library = tmp_path / "library"
    data = tmp_path / "runtime-data"
    runtime = _runtime(library, data)

    runtime.route(
        _msg(
            "folder.create",
            {"topic": "Finances"},
            {"confirmation": {"required": True, "status": "approved", "request_id": "appr-folder"}},
        )
    )
    runtime.route(_msg("folder.switch", {"folder": "finances"}))

    runtime.route(_msg("workflow.interview.start", {}))
    runtime.route(_msg("workflow.interview.continue", {"answer": "First interview answer"}))
    runtime.route(_msg("workflow.interview.complete", {}))

    runtime.route(_msg("workflow.interview.start", {}))
    runtime.route(_msg("workflow.interview.continue", {"answer": "Second interview answer"}))
    runtime.route(_msg("workflow.interview.complete", {}))

    interview_path = library / "finances" / "interview.md"
    assert interview_path.exists()
    content = interview_path.read_text(encoding="utf-8")
    assert content.count("## Interview Session ") >= 2
    assert "- Started: `" in content
    assert "- Completed: `" in content


def test_workflow_settings_reload_without_schema_errors(tmp_path: Path):
    library = tmp_path / "library"
    data = tmp_path / "runtime-data"

    first = _runtime(library, data)
    first.workflow_state.update({"settings": {"theme": "plain", "provider_hint": "openrouter"}})

    second = _runtime(library, data)
    settings = second.workflow_state.read("settings", {})
    assert settings.get("theme") == "plain"
    assert settings.get("provider_hint") == "openrouter"


def test_ad_hoc_chat_text_is_not_persisted(tmp_path: Path):
    library = tmp_path / "library"
    data = tmp_path / "runtime-data"
    runtime = _runtime(library, data)

    marker = "RANDOM-AD-HOC-CHAT-TEXT-DO-NOT-PERSIST"
    runtime.route(_msg("chat.general", {"text": marker}))

    for path in data.rglob("*"):
        if not path.is_file():
            continue
        content = path.read_text(encoding="utf-8", errors="replace")
        assert marker not in content
