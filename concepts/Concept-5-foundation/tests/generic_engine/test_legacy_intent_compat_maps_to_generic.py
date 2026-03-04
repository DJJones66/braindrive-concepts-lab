from __future__ import annotations


def _create_and_switch(runtime, make_message):
    created = runtime.route(
        make_message(
            "folder.create",
            {"topic": "Compat Map"},
            {"confirmation": {"required": True, "status": "approved", "request_id": "appr-compat-map"}},
        )
    )
    assert created["intent"] == "folder.created"
    switched = runtime.route(make_message("folder.switch", {"folder": "compat-map"}))
    assert switched["intent"] == "folder.switched"


def test_legacy_workflow_intent_matches_generic_skill_execute(runtime, make_message):
    _create_and_switch(runtime, make_message)

    legacy = runtime.route(make_message("workflow.spec.generate", {}))
    generic = runtime.route(
        make_message(
            "skill.execute.read",
            {
                "skill_id": "spec-generation",
                "action": "generate",
                "context": {"folder": "compat-map"},
            },
        )
    )

    assert legacy["intent"] == "workflow.spec.generated"
    assert generic["intent"] == "skill.executed"

    generic_result = generic["payload"].get("result", {})
    assert generic_result.get("intent") == "workflow.spec.generated"

    legacy_markdown = legacy["payload"].get("spec_markdown", "")
    generic_markdown = generic_result.get("payload", {}).get("spec_markdown", "")
    assert isinstance(legacy_markdown, str)
    assert isinstance(generic_markdown, str)
    assert legacy_markdown
    assert generic_markdown
