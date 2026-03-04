from __future__ import annotations


def _create_and_switch(runtime, make_message):
    created = runtime.route(
        make_message(
            "folder.create",
            {"topic": "Manifest Driven"},
            {"confirmation": {"required": True, "status": "approved", "request_id": "appr-manifest-driven"}},
        )
    )
    assert created["intent"] == "folder.created"
    switched = runtime.route(make_message("folder.switch", {"folder": "manifest-driven"}))
    assert switched["intent"] == "folder.switched"


def test_operation_dispatch_uses_manifest_metadata(runtime, make_message):
    node = runtime.nodes["node.workflow.skill"].node
    catalog = node._load_catalog()  # noqa: SLF001

    interview = catalog.get("interview", {})
    actions = interview.get("actions", {}) if isinstance(interview, dict) else {}
    start_meta = actions.get("start", {}) if isinstance(actions, dict) else {}

    assert isinstance(start_meta, dict)
    assert start_meta.get("operation") == "session.start"

    _create_and_switch(runtime, make_message)
    response = runtime.route(
        make_message(
            "skill.execute.stateful",
            {
                "skill_id": "interview",
                "action": "start",
                "context": {"folder": "manifest-driven"},
            },
        )
    )

    assert response["intent"] == "skill.executed"
    result = response["payload"].get("result", {})
    assert result.get("intent") == "workflow.interview.question"
