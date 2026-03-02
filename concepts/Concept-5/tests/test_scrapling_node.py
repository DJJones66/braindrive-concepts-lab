from __future__ import annotations

import ipaddress
from pathlib import Path
from typing import Any, Dict, Optional

from braindrive_runtime.protocol import new_uuid
from braindrive_runtime.runtime import BrainDriveRuntime


def _msg(intent: str, payload: Dict[str, Any], extensions: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "protocol_version": "0.1",
        "message_id": new_uuid(),
        "intent": intent,
        "payload": payload,
    }
    if extensions is not None:
        body["extensions"] = extensions
    return body


def _runtime(tmp_path: Path, env_overrides: Optional[Dict[str, str]] = None) -> BrainDriveRuntime:
    env = {
        "BRAINDRIVE_DEFAULT_PROVIDER": "openrouter",
        "BRAINDRIVE_OPENROUTER_API_KEY": "test-key",
        "BRAINDRIVE_OPENROUTER_DEFAULT_MODEL": "anthropic/claude-sonnet-4",
        "BRAINDRIVE_OLLAMA_BASE_URL": "http://localhost:11434/v1",
        "BRAINDRIVE_OLLAMA_DEFAULT_MODEL": "llama3:8b",
        "BRAINDRIVE_ENABLE_TEST_ENDPOINTS": "true",
        "WEBTERM_SSH_AUTH_MODE": "disabled",
    }
    if env_overrides:
        env.update(env_overrides)
    rt = BrainDriveRuntime(library_root=tmp_path / "library", data_root=tmp_path / "runtime-data", env=env)
    rt.bootstrap()
    return rt


def _scrapling_node(runtime):
    item = runtime.nodes.get("node.web.scrapling")
    assert item is not None
    return item.node


class _FakeScraplingBackend:
    def __init__(self, content: str = "example content") -> None:
        self.content = content

    def get(self, **kwargs):  # noqa: ANN003
        return {"status": 200, "url": kwargs["url"], "content": [self.content]}

    def bulk_get(self, **kwargs):  # noqa: ANN003
        return [{"status": 200, "url": url, "content": [self.content]} for url in kwargs["urls"]]

    def fetch(self, **kwargs):  # noqa: ANN003
        return {"status": 200, "url": kwargs["url"], "content": [self.content]}

    def bulk_fetch(self, **kwargs):  # noqa: ANN003
        return [{"status": 200, "url": url, "content": [self.content]} for url in kwargs["urls"]]

    def stealthy_fetch(self, **kwargs):  # noqa: ANN003
        return {"status": 200, "url": kwargs["url"], "content": [self.content]}

    def bulk_stealthy_fetch(self, **kwargs):  # noqa: ANN003
        return [{"status": 200, "url": url, "content": [self.content]} for url in kwargs["urls"]]


class _WrappedResponseBackend:
    @staticmethod
    def get(**kwargs):  # noqa: ANN003
        return {
            "status": 0,
            "url": kwargs["url"],
            "content": ["status=200 content=['hello world', ''] url='https://example.com/test'"],
        }

    @staticmethod
    def bulk_get(**kwargs):  # noqa: ANN003
        return []

    @staticmethod
    def fetch(**kwargs):  # noqa: ANN003
        return {}

    @staticmethod
    def bulk_fetch(**kwargs):  # noqa: ANN003
        return []

    @staticmethod
    def stealthy_fetch(**kwargs):  # noqa: ANN003
        return {}

    @staticmethod
    def bulk_stealthy_fetch(**kwargs):  # noqa: ANN003
        return []


def test_scrapling_metadata_contract(runtime):
    descriptor = runtime.nodes["node.web.scrapling"].descriptor
    by_name = {item.name: item for item in descriptor.capabilities}
    assert by_name["web.scrape.get"].side_effect_scope == "external"
    assert by_name["web.scrape.fetch"].side_effect_scope == "external"
    assert by_name["web.scrape.stealth_fetch"].approval_required is True
    assert by_name["web.scrape.bulk_stealth_fetch"].approval_required is True


def test_scrapling_rejects_missing_url(runtime, make_message):
    response = runtime.route(make_message("web.scrape.get", {}))
    assert response["intent"] == "error"
    assert response["payload"]["error"]["code"] == "E_BAD_MESSAGE"


def test_scrapling_rejects_private_network_targets(runtime, make_message):
    response = runtime.route(make_message("web.scrape.get", {"url": "http://127.0.0.1"}))
    assert response["intent"] == "error"
    assert response["payload"]["error"]["code"] == "E_BAD_MESSAGE"


def test_scrapling_bulk_url_limit_enforced(runtime, make_message):
    payload = {"urls": [f"https://example{i}.com" for i in range(11)]}
    response = runtime.route(make_message("web.scrape.bulk_get", payload))
    assert response["intent"] == "error"
    assert response["payload"]["error"]["code"] == "E_BAD_MESSAGE"


def test_scrapling_rejects_unsafe_callback_fields(runtime, make_message):
    response = runtime.route(
        make_message(
            "web.scrape.get",
            {
                "url": "https://example.com",
                "page_action": "alert(1)",
            },
        )
    )
    assert response["intent"] == "error"
    assert response["payload"]["error"]["code"] == "E_BAD_MESSAGE"


def test_scrapling_stealth_requires_confirmation(runtime, make_message):
    response = runtime.route(make_message("web.scrape.stealth_fetch", {"url": "https://example.com"}))
    assert response["intent"] == "error"
    assert response["payload"]["error"]["code"] == "E_CONFIRMATION_REQUIRED"


def test_scrapling_truncates_content(monkeypatch, runtime, make_message):
    node = _scrapling_node(runtime)
    monkeypatch.setattr(node, "backend", _FakeScraplingBackend("abcdefghijklmnopqrstuvwxyz"))
    monkeypatch.setattr(node, "_resolve_host_ips", lambda host: [ipaddress.ip_address("93.184.216.34")])

    response = runtime.route(
        make_message(
            "web.scrape.get",
            {
                "url": "https://example.com",
                "max_content_chars": 12,
            },
        )
    )

    assert response["intent"] == "web.scrape.completed"
    payload = response["payload"]
    assert payload["truncated"] is True
    assert payload["limits"]["max_content_chars"] == 12
    assert payload["results"][0]["content"][0] == "abcdefghijkl"


def test_scrapling_allowed_domain_policy(monkeypatch, tmp_path: Path):
    runtime = _runtime(tmp_path, {"BRAINDRIVE_SCRAPLING_ALLOWED_DOMAINS": "example.com"})
    node = _scrapling_node(runtime)
    monkeypatch.setattr(node, "backend", _FakeScraplingBackend())
    monkeypatch.setattr(node, "_resolve_host_ips", lambda host: [ipaddress.ip_address("93.184.216.34")])

    denied = runtime.route(_msg("web.scrape.get", {"url": "https://not-example.com"}))
    assert denied["intent"] == "error"
    assert denied["payload"]["error"]["code"] == "E_BAD_MESSAGE"

    allowed = runtime.route(_msg("web.scrape.get", {"url": "https://example.com"}))
    assert allowed["intent"] == "web.scrape.completed"


def test_scrapling_parses_wrapped_result_representation(monkeypatch, runtime, make_message):
    node = _scrapling_node(runtime)
    monkeypatch.setattr(node, "backend", _WrappedResponseBackend())
    monkeypatch.setattr(node, "_resolve_host_ips", lambda host: [ipaddress.ip_address("93.184.216.34")])

    response = runtime.route(make_message("web.scrape.get", {"url": "https://example.com"}))
    assert response["intent"] == "web.scrape.completed"
    item = response["payload"]["results"][0]
    assert item["status"] == 200
    assert item["url"] == "https://example.com/test"
    assert item["content"] == ["hello world"]


def test_scrapling_default_saves_to_library_scraping_dir(monkeypatch, runtime, make_message):
    node = _scrapling_node(runtime)
    monkeypatch.setattr(node, "backend", _FakeScraplingBackend("saved content"))
    monkeypatch.setattr(node, "_resolve_host_ips", lambda host: [ipaddress.ip_address("93.184.216.34")])

    response = runtime.route(make_message("web.scrape.get", {"url": "https://example.com"}))
    assert response["intent"] == "web.scrape.completed"

    payload = response["payload"]
    storage = payload["storage"]
    assert storage["saved"] is True
    assert storage["directory"] == "scraping"
    assert len(storage["files"]) == 1

    relative = storage["files"][0]["path"]
    assert relative.startswith("scraping/")
    saved_file = runtime.library_root / relative
    assert saved_file.exists()
    assert saved_file.read_text(encoding="utf-8") == "saved content"


def test_scrapling_save_can_be_disabled_in_payload(monkeypatch, runtime, make_message):
    node = _scrapling_node(runtime)
    monkeypatch.setattr(node, "backend", _FakeScraplingBackend("unsaved content"))
    monkeypatch.setattr(node, "_resolve_host_ips", lambda host: [ipaddress.ip_address("93.184.216.34")])

    response = runtime.route(
        make_message(
            "web.scrape.get",
            {
                "url": "https://example.com",
                "save_to_library": False,
            },
        )
    )
    assert response["intent"] == "web.scrape.completed"
    storage = response["payload"]["storage"]
    assert storage["saved"] is False
    assert storage["files"] == []
    assert not (runtime.library_root / "scraping").exists()
