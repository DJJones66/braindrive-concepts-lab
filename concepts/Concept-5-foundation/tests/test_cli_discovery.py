from __future__ import annotations

from scripts.cli import CliClient


def _client() -> CliClient:
    return CliClient(
        router_base="http://router",
        intent_base="http://intent",
        gateway_base="http://gateway",
        timeout_sec=1.0,
    )


def _registry_payload():
    return [
        {
            "node_id": "node.workflow.folder",
            "capabilities": [
                {
                    "name": "folder.list",
                    "description": "List available folders",
                    "input_schema": {"type": "object"},
                    "examples": ["get active folder"],
                    "visibility": "public",
                },
                {
                    "name": "folder.switch",
                    "description": "Switch active folder context",
                    "input_schema": {"type": "object", "required": ["folder"]},
                    "examples": ["switch to finances"],
                    "visibility": "public",
                }
            ],
        },
        {
            "node_id": "node.session.state",
            "capabilities": [
                {
                    "name": "session.active_folder.get",
                    "description": "Read session active folder",
                    "input_schema": {"type": "object"},
                    "examples": ["session active folder get"],
                    "visibility": "internal",
                },
                {
                    "name": "debug.internal.list",
                    "description": "Internal debug command",
                    "input_schema": {"type": "object"},
                    "examples": ["internal debug"],
                    "visibility": "internal",
                }
            ],
        },
    ]


def test_commands_search_hides_internal_session_terms_and_shows_additional_capabilities(monkeypatch, capsys):
    client = _client()
    monkeypatch.setattr(client, "router_registry", lambda: _registry_payload())
    monkeypatch.setattr(client, "router_catalog", lambda: {})

    client.handle_commands_search("folder")
    output = capsys.readouterr().out

    assert "get active folder" in output
    assert "switch to <folder>" in output
    assert "session.active_folder.get" not in output
    assert "debug.internal.list" not in output


def test_prompts_excludes_internal_session_capabilities(monkeypatch, capsys):
    client = _client()
    client.prompts_page_size = 300
    monkeypatch.setattr(client, "router_registry", lambda: _registry_payload())
    monkeypatch.setattr(client, "router_catalog", lambda: {})

    client.handle_prompts_command("all")
    output = capsys.readouterr().out

    assert "folder.list" in output
    assert "session.active_folder.get" not in output
