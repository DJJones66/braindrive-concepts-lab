from __future__ import annotations

from pathlib import Path

from braindrive_runtime.protocol import new_uuid
from braindrive_runtime.runtime import BrainDriveRuntime


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


def test_manifest_actions_require_operation_field(tmp_path: Path):
    library = tmp_path / "library"
    skills_dir = library / ".braindrive" / "skills" / "bad-skill"
    prompts_dir = skills_dir / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)

    # Missing required operation field.
    (skills_dir / "skill.yaml").write_text(
        """
skill_id: bad-skill
version: 1.0.0
actions:
  run:
    execution_tier: read
    prompt_template: prompts/run.md
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (prompts_dir / "run.md").write_text("# Bad Skill\n", encoding="utf-8")

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

    catalog = rt.route(_msg("skill.catalog.list", {}))
    assert catalog["intent"] == "skill.catalog"
    listed = [item.get("skill_id") for item in catalog["payload"].get("skills", []) if isinstance(item, dict)]
    assert "bad-skill" not in listed

    response = rt.route(
        _msg(
            "skill.execute.read",
            {"skill_id": "bad-skill", "action": "run"},
        )
    )
    assert response["intent"] == "error"
    assert response["payload"]["error"]["code"] == "E_NODE_ERROR"
