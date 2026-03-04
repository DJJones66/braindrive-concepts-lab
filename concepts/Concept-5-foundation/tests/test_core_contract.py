from __future__ import annotations


def test_rejects_missing_message_id(runtime, make_message):
    message = {
        "protocol_version": "0.1",
        "intent": "chat.general",
        "payload": {"text": "hello"},
    }
    response = runtime.route(message)
    assert response["intent"] == "error"
    assert response["payload"]["error"]["code"] == "E_BAD_MESSAGE"


def test_rejects_non_object_payload(runtime, make_message):
    message = make_message("chat.general", payload={"text": "ok"})
    message["payload"] = "bad"
    response = runtime.route(message)
    assert response["intent"] == "error"
    assert response["payload"]["error"]["code"] == "E_BAD_MESSAGE"


def test_accepts_valid_message_and_routes(runtime, make_message):
    response = runtime.route(make_message("chat.general", {"text": "hello"}))
    assert response["intent"] == "chat.response"
    assert response["payload"]["text"] == "hello"
