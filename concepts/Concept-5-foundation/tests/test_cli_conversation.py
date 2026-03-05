from __future__ import annotations

import json

from scripts import cli as cli_module


def _client():
    return cli_module.CliClient(
        router_base="http://router",
        intent_base="http://intent",
        gateway_base="http://gateway",
        timeout_sec=1.0,
    )


def test_route_text_reuses_same_conversation_id(monkeypatch):
    client = _client()
    captured_payloads = []

    def _fake_request(method: str, url: str, timeout_sec: float, payload=None):  # noqa: ANN001
        assert method == "POST"
        assert url.endswith("/api/v1/messages")
        captured_payloads.append(dict(payload or {}))
        return {
            "ok": True,
            "conversation_id": str(payload.get("conversation_id", "")),
            "status": "routed",
            "analysis": {"canonical_intent": "model.chat.complete"},
            "route_message": {"intent": "model.chat.complete"},
            "route_response": {"intent": "model.chat.completed", "payload": {"text": "ok"}},
        }

    monkeypatch.setattr(cli_module, "_request", _fake_request)

    first = client.route_text("Tell me a joke")
    second = client.route_text("Can you explain the last joke to me?")

    assert first["ok"] is True
    assert second["ok"] is True
    assert len(captured_payloads) == 2

    first_id = str(captured_payloads[0].get("conversation_id", "")).strip()
    second_id = str(captured_payloads[1].get("conversation_id", "")).strip()
    assert first_id
    assert first_id == second_id
    assert client.conversation_id == first_id


def test_model_chat_complete_streams_for_fallback_reason():
    client = _client()
    client.stream_fallback_only = True
    analysis = {
        "canonical_intent": "model.chat.complete",
        "clarification_required": False,
        "reason_codes": ["fallback_model_chat"],
        "payload": {"prompt": "hello"},
    }

    assert client._analysis_is_streamable_model_chat(analysis) is True


def test_model_chat_stream_still_streamable():
    client = _client()
    client.stream_fallback_only = True
    analysis = {
        "canonical_intent": "model.chat.stream",
        "clarification_required": False,
        "reason_codes": ["keyword_model_stream"],
        "payload": {"prompt": "hello"},
    }

    assert client._analysis_is_streamable_model_chat(analysis) is True


def test_stream_chat_record_persists_and_history_loads(tmp_path):
    client = _client()
    client.library_root = tmp_path / "library"
    client.conversation_id = "conv_cli_test"

    client._append_stream_chat_record(input_text="Tell me a joke", output_text="Because they make up everything.", complete=True)

    jsonl_path = client.library_root / "chats" / "conv_cli_test.jsonl"
    meta_path = client.library_root / "chats" / "conv_cli_test.meta.json"
    assert jsonl_path.exists()
    assert meta_path.exists()

    records = [json.loads(line) for line in jsonl_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(records) == 1
    assert records[0]["input"]["text"] == "Tell me a joke"
    assert records[0]["output"]["text"] == "Because they make up everything."

    history = client._load_provider_history_messages("conv_cli_test", max_turns=12, max_chars=12000)
    assert history == [
        {"role": "user", "content": "Tell me a joke"},
        {"role": "assistant", "content": "Because they make up everything."},
    ]
