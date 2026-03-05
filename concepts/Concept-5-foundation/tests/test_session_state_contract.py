from __future__ import annotations


def test_session_active_folder_set_requires_internal_extension(runtime, make_message):
    response = runtime.route(make_message("session.active_folder.set", {"active_folder": "dimes"}))
    assert response["intent"] == "error"
    assert response["payload"]["error"]["code"] == "E_REQUIRED_EXTENSION_MISSING"


def test_folder_switch_still_updates_active_folder_via_internal_path(runtime):
    created = runtime.route_nl("create folder dimes", confirm=True)
    assert created["status"] == "routed"
    assert created["route_response"]["intent"] == "folder.created"

    switched = runtime.route_nl("switch folder to dimes")
    assert switched["status"] == "routed"
    assert switched["route_response"]["intent"] == "folder.switched"

    current = runtime.route_nl("get active folder")
    assert current["status"] == "routed"
    assert current["route_response"]["intent"] == "folder.current"
    assert current["route_response"]["payload"]["active_folder"] == "dimes"


def test_session_capabilities_are_internal_and_mutations_are_mutate_class(runtime):
    descriptor = runtime.nodes["node.session.state"].descriptor
    by_name = {item.name: item for item in descriptor.capabilities}

    assert by_name["session.active_folder.get"].visibility == "internal"
    assert by_name["session.active_folder.set"].visibility == "internal"
    assert by_name["session.active_folder.set"].required_extensions == ["internal"]
    assert by_name["session.active_folder.set"].risk_class == "mutate"
    assert by_name["session.interview.put"].risk_class == "mutate"
    assert by_name["session.skill_output.put"].risk_class == "mutate"

