from __future__ import annotations

from typing import Any, Dict

from braindrive_runtime.metadata import CapabilityMetadata, NodeDescriptor
from braindrive_runtime.protocol import make_response, new_uuid


def _register_custom(runtime, node_id: str, priority: int, version: str, capability: CapabilityMetadata, handler):
    descriptor = NodeDescriptor(
        node_id=node_id,
        node_version=version,
        endpoint_url=f"inproc://{node_id}",
        supported_protocol_versions=["0.1"],
        capabilities=[capability],
        requires=[],
        priority=priority,
        auth={"registration_token": runtime.registration_token},
    )
    result = runtime.router.register_node(descriptor, handler)
    assert result["ok"] is True


def test_deterministic_selection(runtime, make_message):
    cap = CapabilityMetadata(
        name="determinism.echo",
        description="deterministic test",
        input_schema={"type": "object"},
        risk_class="read",
        required_extensions=[],
        approval_required=False,
        examples=["echo"],
        idempotency="idempotent",
        side_effect_scope="none",
        capability_version="0.1.0",
    )

    _register_custom(
        runtime,
        "node.det.z",
        200,
        "1.0.0",
        cap,
        lambda msg: make_response("determinism.result", {"selected": "z"}, msg.get("message_id")),
    )
    _register_custom(
        runtime,
        "node.det.a",
        200,
        "1.2.0",
        cap,
        lambda msg: make_response("determinism.result", {"selected": "a"}, msg.get("message_id")),
    )

    response = runtime.route(make_message("determinism.echo", {}))
    assert response["intent"] == "determinism.result"
    assert response["payload"]["selected"] == "a"


def test_no_route_error(runtime, make_message):
    response = runtime.route(make_message("unknown.capability", {}))
    assert response["intent"] == "error"
    assert response["payload"]["error"]["code"] == "E_NO_ROUTE"


def test_required_extension_enforcement(runtime, make_message):
    cap = CapabilityMetadata(
        name="extension.required",
        description="requires identity",
        input_schema={"type": "object"},
        risk_class="read",
        required_extensions=["identity"],
        approval_required=False,
        examples=["extension test"],
        idempotency="idempotent",
        side_effect_scope="none",
        capability_version="0.1.0",
    )
    _register_custom(
        runtime,
        "node.extension",
        100,
        "0.1.0",
        cap,
        lambda msg: make_response("extension.ok", {"ok": True}, msg.get("message_id")),
    )

    missing = runtime.route(make_message("extension.required", {}))
    assert missing["intent"] == "error"
    assert missing["payload"]["error"]["code"] == "E_REQUIRED_EXTENSION_MISSING"

    ok = runtime.route(make_message("extension.required", {}, {"identity": {"actor_id": "u"}}))
    assert ok["intent"] == "extension.ok"


def test_safety_is_metadata_driven(runtime, make_message):
    cap = CapabilityMetadata(
        name="dynamic.mutate",
        description="dynamic mutate capability",
        input_schema={"type": "object"},
        risk_class="mutate",
        required_extensions=[],
        approval_required=True,
        examples=["dynamic mutate"],
        idempotency="non_idempotent",
        side_effect_scope="file",
        capability_version="0.1.0",
    )
    _register_custom(
        runtime,
        "node.dynamic.mutate",
        300,
        "0.1.0",
        cap,
        lambda msg: make_response("dynamic.mutate.done", {"ok": True}, msg.get("message_id")),
    )

    blocked = runtime.route(make_message("dynamic.mutate", {}))
    assert blocked["intent"] == "error"
    assert blocked["payload"]["error"]["code"] == "E_CONFIRMATION_REQUIRED"

    allowed = runtime.route(
        make_message(
            "dynamic.mutate",
            {},
            {"confirmation": {"required": True, "status": "approved", "request_id": "appr-1"}},
        )
    )
    assert allowed["intent"] == "dynamic.mutate.done"


def test_new_capability_discovery_and_routing(runtime, make_message):
    cap = CapabilityMetadata(
        name="dynamic.added",
        description="added at runtime",
        input_schema={"type": "object"},
        risk_class="read",
        required_extensions=[],
        approval_required=False,
        examples=["dynamic added"],
        idempotency="idempotent",
        side_effect_scope="none",
        capability_version="0.1.0",
    )
    _register_custom(
        runtime,
        "node.dynamic.added",
        90,
        "0.1.0",
        cap,
        lambda msg: make_response("dynamic.added.ok", {"ok": True}, msg.get("message_id")),
    )

    catalog = runtime.router.catalog()
    assert "dynamic.added" in catalog

    routed = runtime.route(make_message("dynamic.added", {}))
    assert routed["intent"] == "dynamic.added.ok"


def test_provider_pinning_for_model_intents(runtime, make_message):
    ollama = runtime.route(
        make_message(
            "model.chat.complete",
            {"prompt": "hello"},
            {"llm": {"provider": "ollama", "model": "llama3:8b"}},
        )
    )
    assert ollama["intent"] == "model.chat.completed"
    assert ollama["payload"]["provider"] == "ollama"

    openrouter = runtime.route(
        make_message(
            "model.chat.complete",
            {"prompt": "hello"},
            {"llm": {"provider": "openrouter", "model": "anthropic/claude-sonnet-4"}},
        )
    )
    assert openrouter["intent"] == "model.chat.completed"
    assert openrouter["payload"]["provider"] == "openrouter"


def test_unknown_prompt_defaults_to_model_chat(runtime):
    routed = runtime.route_nl("Tell me what I should focus on this week")
    assert routed["status"] == "routed"
    assert routed["analysis"]["canonical_intent"] == "model.chat.complete"
    assert routed["route_response"]["intent"] == "model.chat.completed"


def test_model_chat_payload_includes_provider_history_from_context(runtime):
    analyzed = runtime.intent_router.analyze(
        "Tell me what I should focus on this week",
        context={
            "provider_history_messages": [
                {"role": "user", "content": "Earlier question"},
                {"role": "assistant", "content": "Earlier answer"},
            ]
        },
    )
    assert analyzed["canonical_intent"] == "model.chat.complete"
    assert analyzed["payload"]["messages"] == [
        {"role": "user", "content": "Earlier question"},
        {"role": "assistant", "content": "Earlier answer"},
        {"role": "user", "content": "Tell me what I should focus on this week"},
    ]


def test_list_my_folders_routes_to_folder_list(runtime):
    routed = runtime.route_nl("can you list my folders")
    assert routed["status"] == "routed"
    assert routed["analysis"]["canonical_intent"] == "folder.list"
    assert routed["route_response"]["intent"] == "folder.listed"


def test_get_active_folder_phrase_routes_to_folder_read(runtime):
    routed = runtime.route_nl("get active folder")
    assert routed["status"] == "routed"
    assert routed["analysis"]["canonical_intent"] == "folder.current.get"
    assert routed["analysis"]["reason_codes"] == ["keyword_active_folder_get"]
    assert routed["route_response"]["intent"] == "folder.current"


def test_set_active_folder_phrase_routes_to_folder_switch(runtime):
    created = runtime.route_nl("create folder dimes", confirm=True)
    assert created["status"] == "routed"
    assert created["route_response"]["intent"] == "folder.created"

    routed = runtime.route_nl("set active folder dimes")
    assert routed["status"] == "routed"
    assert routed["analysis"]["canonical_intent"] == "folder.switch"
    assert routed["analysis"]["reason_codes"] == ["keyword_active_folder_set"]
    assert routed["analysis"]["payload"]["folder"] == "dimes"
    assert routed["route_response"]["intent"] == "folder.switched"


def test_set_current_active_folder_phrase_routes_to_folder_switch(runtime):
    created = runtime.route_nl("create folder dimes", confirm=True)
    assert created["status"] == "routed"
    assert created["route_response"]["intent"] == "folder.created"

    routed = runtime.route_nl("set current active folder to dimes")
    assert routed["status"] == "routed"
    assert routed["analysis"]["canonical_intent"] == "folder.switch"
    assert routed["analysis"]["payload"]["folder"] == "dimes"
    assert routed["route_response"]["intent"] == "folder.switched"


def test_set_active_folder_phrase_uses_same_validation_as_folder_switch(runtime):
    routed = runtime.route_nl("set active folder missing-folder")
    assert routed["status"] == "route_error"
    assert routed["analysis"]["canonical_intent"] == "folder.switch"
    assert routed["route_response"]["intent"] == "error"
    assert routed["route_response"]["payload"]["error"]["code"] == "E_NODE_ERROR"


def test_folder_current_get_returns_exists_and_context_docs(runtime, make_message):
    created = runtime.route_nl("create folder dimes", confirm=True)
    assert created["status"] == "routed"
    assert created["route_response"]["intent"] == "folder.created"

    switched = runtime.route_nl("switch folder to dimes")
    assert switched["status"] == "routed"
    assert switched["route_response"]["intent"] == "folder.switched"

    current = runtime.route(make_message("folder.current.get", {}))
    assert current["intent"] == "folder.current"
    assert current["payload"]["active_folder"] == "dimes"
    assert current["payload"]["exists"] is True
    assert isinstance(current["payload"]["context_docs"], dict)


def test_list_files_scopes_to_active_folder(runtime):
    created = runtime.route_nl("create folder dimes", confirm=True)
    assert created["status"] == "routed"
    assert created["route_response"]["intent"] == "folder.created"

    switched = runtime.route_nl("switch folder to dimes")
    assert switched["status"] == "routed"
    assert switched["route_response"]["intent"] == "folder.switched"

    routed = runtime.route_nl("list files")
    assert routed["status"] == "routed"
    assert routed["analysis"]["canonical_intent"] == "memory.list"
    assert routed["analysis"]["payload"]["path"] == "dimes"
    assert routed["route_response"]["intent"] == "memory.listed"


def test_plain_text_routes_to_interview_continue_when_context_awaiting_answer(runtime):
    created = runtime.route_nl("create folder dimes", confirm=True)
    assert created["status"] == "routed"
    assert created["route_response"]["intent"] == "folder.created"

    switched = runtime.route_nl("switch folder to dimes")
    assert switched["status"] == "routed"
    assert switched["route_response"]["intent"] == "folder.switched"

    started = runtime.route_nl("start interview")
    assert started["status"] == "routed"
    assert started["route_response"]["intent"] == "workflow.interview.question"

    routed = runtime.intent_router.route(
        "I want to collect silver dimes from the 1960s",
        context={"interview": {"awaiting_answer": True}},
    )
    assert routed["status"] == "routed"
    assert routed["analysis"]["canonical_intent"] == "workflow.interview.continue"
    assert routed["analysis"]["payload"]["answer"] == "I want to collect silver dimes from the 1960s"
    assert routed["route_response"]["intent"] in {"workflow.interview.question", "workflow.interview.ready"}


def test_plain_text_routes_to_interview_continue_without_context_flag(runtime):
    created = runtime.route_nl("create folder dimes", confirm=True)
    assert created["status"] == "routed"
    assert created["route_response"]["intent"] == "folder.created"

    switched = runtime.route_nl("switch folder to dimes")
    assert switched["status"] == "routed"
    assert switched["route_response"]["intent"] == "folder.switched"

    started = runtime.route_nl("start interview")
    assert started["status"] == "routed"
    assert started["route_response"]["intent"] == "workflow.interview.question"

    analyzed = runtime.intent_router.analyze(
        "I want to collect silver dimes from the 1960s",
        context={"active_folder": "dimes"},
    )
    assert analyzed["canonical_intent"] == "workflow.interview.continue"
    assert analyzed["payload"]["answer"] == "I want to collect silver dimes from the 1960s"


def test_plain_text_routes_to_interview_continue_without_any_context(runtime):
    created = runtime.route_nl("create folder dimes", confirm=True)
    assert created["status"] == "routed"
    assert created["route_response"]["intent"] == "folder.created"

    switched = runtime.route_nl("switch folder to dimes")
    assert switched["status"] == "routed"
    assert switched["route_response"]["intent"] == "folder.switched"

    started = runtime.route_nl("start interview")
    assert started["status"] == "routed"
    assert started["route_response"]["intent"] == "workflow.interview.question"

    analyzed = runtime.intent_router.analyze("I want to collect silver dimes from the 1960s")
    assert analyzed["canonical_intent"] == "workflow.interview.continue"
    assert analyzed["payload"]["answer"] == "I want to collect silver dimes from the 1960s"


def test_interview_context_overrides_work_on_phrase(runtime):
    analyzed = runtime.intent_router.analyze(
        "I own 2 of these and want to work on projects to learn them better",
        context={"interview": {"awaiting_answer": True}},
    )
    assert analyzed["canonical_intent"] == "workflow.interview.continue"
    assert analyzed["payload"]["answer"] == "I own 2 of these and want to work on projects to learn them better"


def test_complete_interview_still_routes_when_awaiting_answer(runtime):
    analyzed = runtime.intent_router.analyze(
        "complete interview",
        context={"interview": {"awaiting_answer": True}},
    )
    assert analyzed["canonical_intent"] == "workflow.interview.complete"


def test_create_folder_parses_topic_without_command_words(runtime):
    analyzed = runtime.analyze("create folder Coins")
    assert analyzed["canonical_intent"] == "folder.create"
    assert analyzed["payload"]["topic"] == "Coins"


def test_create_folder_parses_quoted_topic(runtime):
    analyzed = runtime.analyze('create folder "Pennies"')
    assert analyzed["canonical_intent"] == "folder.create"
    assert analyzed["payload"]["topic"] == "Pennies"


def test_scrape_prompt_routes_to_scrape_get_analysis(runtime):
    analyzed = runtime.analyze("scrape https://example.com")
    assert analyzed["canonical_intent"] == "web.scrape.get"
    assert analyzed["payload"]["url"] == "https://example.com"
    assert analyzed["payload"]["extraction_type"] == "markdown"


def test_dynamic_scrape_prompt_routes_to_fetch_analysis(runtime):
    analyzed = runtime.analyze("fetch dynamic content from https://example.com")
    assert analyzed["canonical_intent"] == "web.scrape.fetch"
    assert analyzed["payload"]["url"] == "https://example.com"


def test_stealth_scrape_prompt_requires_confirmation_analysis(runtime):
    analyzed = runtime.analyze("use stealth scrape for https://example.com")
    assert analyzed["canonical_intent"] == "web.scrape.stealth_fetch"
    assert analyzed["required_confirmation"] is True


def test_bulk_scrape_prompt_routes_to_bulk_analysis(runtime):
    analyzed = runtime.analyze("scrape https://a.example.com and https://b.example.com")
    assert analyzed["canonical_intent"] == "web.scrape.bulk_get"
    assert len(analyzed["payload"]["urls"]) == 2


def test_scrape_missing_url_returns_clarification(runtime):
    analyzed = runtime.analyze("scrape this page for me")
    assert analyzed["clarification_required"] is True
    assert "URL" in analyzed["clarification_prompt"]

    routed = runtime.route_nl("scrape this page for me")
    assert routed["status"] == "needs_clarification"


def test_web_console_open_requires_origin_context(runtime):
    analyzed = runtime.analyze("open web terminal")
    assert analyzed["canonical_intent"] == "web.console.session.open"
    assert analyzed["clarification_required"] is True

    routed = runtime.route_nl("open web terminal")
    assert routed["status"] == "needs_clarification"


def test_web_console_targets_analysis(runtime):
    analyzed = runtime.analyze("list web terminal targets")
    assert analyzed["canonical_intent"] == "web.console.targets.list"
