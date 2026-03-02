from __future__ import annotations


def _create_and_switch(runtime, make_message):
    created = runtime.route(
        make_message(
            "folder.create",
            {"topic": "Skill Lab"},
            {"confirmation": {"required": True, "status": "approved", "request_id": "appr-skill-lab"}},
        )
    )
    assert created["intent"] == "folder.created"

    switched = runtime.route(make_message("folder.switch", {"folder": "skill-lab"}))
    assert switched["intent"] == "folder.switched"


def test_skill_catalog_and_legacy_capability_mapping(runtime, make_message):
    response = runtime.route(make_message("skill.catalog.list", {}))
    assert response["intent"] == "skill.catalog"

    skills = response["payload"].get("skills", [])
    by_skill = {item["skill_id"]: item for item in skills if isinstance(item, dict) and isinstance(item.get("skill_id"), str)}
    assert "interview" in by_skill
    assert "spec-generation" in by_skill
    assert "plan-generation" in by_skill
    assert "start" in by_skill["interview"].get("actions", [])

    catalog = runtime.router.catalog()
    interview_entries = catalog.get("workflow.interview.start", [])
    assert interview_entries
    assert interview_entries[0]["node_id"] == "node.workflow.skill"

    all_node_ids = {entry["node_id"] for entries in catalog.values() for entry in entries if isinstance(entry, dict)}
    assert "node.workflow.interview" not in all_node_ids
    assert "node.workflow.spec" not in all_node_ids
    assert "node.workflow.plan" not in all_node_ids


def test_skill_execute_stateful_interview_start(runtime, make_message):
    _create_and_switch(runtime, make_message)

    response = runtime.route(
        make_message(
            "skill.execute.stateful",
            {
                "skill_id": "interview",
                "action": "start",
                "context": {"folder": "skill-lab"},
                "inputs": {},
            },
        )
    )

    assert response["intent"] == "skill.executed"
    payload = response["payload"]
    assert payload["skill_id"] == "interview"
    assert payload["action"] == "start"
    assert payload["status"] == "ok"

    result = payload.get("result", {})
    assert result.get("intent") == "workflow.interview.question"
    assert isinstance(result.get("payload", {}).get("question", ""), str)


def test_skill_execute_tier_mismatch_fails(runtime, make_message):
    response = runtime.route(
        make_message(
            "skill.execute.read",
            {
                "skill_id": "interview",
                "action": "start",
            },
        )
    )

    assert response["intent"] == "error"
    assert response["payload"]["error"]["code"] == "E_BAD_MESSAGE"
