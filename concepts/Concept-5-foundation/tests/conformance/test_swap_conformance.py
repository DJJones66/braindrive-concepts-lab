from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, List

from braindrive_runtime.protocol import new_uuid

REPORT_ROWS: List[Dict[str, Any]] = []


def _msg(intent: str, payload: Dict[str, Any], extensions: Dict[str, Any] | None = None) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "protocol_version": "0.1",
        "message_id": new_uuid(),
        "intent": intent,
        "payload": payload,
    }
    if extensions is not None:
        body["extensions"] = extensions
    return body


def _source_hashes() -> Dict[str, str]:
    repo = Path(__file__).resolve().parents[2]
    tracked = [
        repo / "braindrive_runtime" / "nodes" / "model_openrouter.py",
        repo / "braindrive_runtime" / "nodes" / "model_ollama.py",
        repo / "braindrive_runtime" / "providers" / "openrouter.py",
        repo / "braindrive_runtime" / "providers" / "ollama.py",
        repo / "braindrive_runtime" / "nodes" / "scrapling.py",
    ]
    out: Dict[str, str] = {}
    for path in tracked:
        out[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def _record(name: str, detail: str) -> None:
    REPORT_ROWS.append({"scenario": name, "status": "pass", "detail": detail})


def test_provider_swap_is_config_only(runtime) -> None:
    before = _source_hashes()

    openrouter = runtime.route(
        _msg(
            "model.chat.complete",
            {"prompt": "provider swap check"},
            {"llm": {"provider": "openrouter", "model": "anthropic/claude-sonnet-4"}},
        )
    )
    ollama = runtime.route(
        _msg(
            "model.chat.complete",
            {"prompt": "provider swap check"},
            {"llm": {"provider": "ollama", "model": "llama3:8b"}},
        )
    )

    assert openrouter["intent"] == "model.chat.completed"
    assert ollama["intent"] == "model.chat.completed"
    assert openrouter["payload"]["provider"] == "openrouter"
    assert ollama["payload"]["provider"] == "ollama"
    assert _source_hashes() == before

    _record("provider_swap", "OpenRouter <-> Ollama succeeded with no source edits")


def test_model_swap_within_provider_is_config_only(runtime) -> None:
    before = _source_hashes()

    first = runtime.route(
        _msg(
            "model.chat.complete",
            {"prompt": "model one"},
            {"llm": {"provider": "openrouter", "model": "anthropic/claude-sonnet-4"}},
        )
    )
    second = runtime.route(
        _msg(
            "model.chat.complete",
            {"prompt": "model two"},
            {"llm": {"provider": "openrouter", "model": "openai/gpt-4.1-mini"}},
        )
    )

    assert first["intent"] == "model.chat.completed"
    assert second["intent"] == "model.chat.completed"
    assert first["payload"]["model"] == "anthropic/claude-sonnet-4"
    assert second["payload"]["model"] == "openai/gpt-4.1-mini"
    assert _source_hashes() == before

    _record("model_swap", "Model override inside one provider stayed config-only")


def test_tool_backend_swap_is_config_only(runtime) -> None:
    before = _source_hashes()
    node = runtime.nodes["node.web.scrapling"].node

    class _BackendA:
        def get(self, **kwargs):
            return {"status": 200, "url": kwargs["url"], "content": ["alpha"]}

    class _BackendB:
        def get(self, **kwargs):
            return {"status": 200, "url": kwargs["url"], "content": ["beta"]}

    node.backend = _BackendA()
    first = runtime.route(
        _msg(
            "web.scrape.get",
            {
                "url": "https://example.com",
                "save_to_library": False,
            },
        )
    )

    node.backend = _BackendB()
    second = runtime.route(
        _msg(
            "web.scrape.get",
            {
                "url": "https://example.com",
                "save_to_library": False,
            },
        )
    )

    assert first["intent"] == "web.scrape.completed"
    assert second["intent"] == "web.scrape.completed"
    first_content = first["payload"]["results"][0]["content"][0]
    second_content = second["payload"]["results"][0]["content"][0]
    assert first_content == "alpha"
    assert second_content == "beta"
    assert _source_hashes() == before

    _record("tool_backend_swap", "Scrapling backend changed without core runtime source edits")


def test_zzz_write_conformance_report_artifact(tmp_path) -> None:
    assert len(REPORT_ROWS) >= 3
    report = {
        "suite": "D147-style config-only swap conformance",
        "scenarios": REPORT_ROWS,
    }

    report_path = os.getenv("BRAINDRIVE_CONFORMANCE_REPORT", "")
    if report_path:
        target = Path(report_path)
    else:
        target = tmp_path / "conformance-report.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8")

    assert target.exists()
