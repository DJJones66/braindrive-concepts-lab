from __future__ import annotations

from typing import Any, Dict, List


def parse_timeout(raw: str) -> float:
    try:
        return max(1.0, float(raw))
    except (TypeError, ValueError):
        return 30.0


def content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        out: List[str] = []
        for item in content:
            if isinstance(item, str):
                out.append(item)
                continue
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    out.append(text)
                    continue
                nested = item.get("content")
                if isinstance(nested, str):
                    out.append(nested)
        return "".join(out).strip()
    return ""


def apply_generation_options(body: Dict[str, Any], llm: Dict[str, Any]) -> None:
    if isinstance(llm.get("max_tokens"), int) and int(llm["max_tokens"]) > 0:
        body["max_tokens"] = int(llm["max_tokens"])
    if isinstance(llm.get("temperature"), (int, float)):
        body["temperature"] = float(llm["temperature"])
    if isinstance(llm.get("top_p"), (int, float)):
        body["top_p"] = float(llm["top_p"])
    if isinstance(llm.get("stop"), str) and llm["stop"].strip():
        body["stop"] = llm["stop"].strip()
    if isinstance(llm.get("stop"), list):
        stops = [str(item).strip() for item in llm["stop"] if str(item).strip()]
        if stops:
            body["stop"] = stops


def extract_choice_text_and_tools(response_body: Dict[str, Any]) -> tuple[str, List[Dict[str, Any]]]:
    choices = response_body.get("choices")
    if not isinstance(choices, list) or not choices:
        return "", []

    first = choices[0] if isinstance(choices[0], dict) else {}
    message = first.get("message") if isinstance(first.get("message"), dict) else {}
    delta = first.get("delta") if isinstance(first.get("delta"), dict) else {}

    text = content_to_text(message.get("content"))
    if not text:
        text = content_to_text(delta.get("content"))

    tool_calls_raw = message.get("tool_calls")
    if not isinstance(tool_calls_raw, list):
        tool_calls_raw = delta.get("tool_calls")
    if not isinstance(tool_calls_raw, list):
        return text, []

    tool_calls: List[Dict[str, Any]] = []
    for item in tool_calls_raw:
        if isinstance(item, dict):
            tool_calls.append(item)
    return text, tool_calls
