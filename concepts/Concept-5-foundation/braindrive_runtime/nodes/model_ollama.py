from __future__ import annotations

import os
from typing import Any, Dict, List

from ..constants import E_NODE_TIMEOUT, MODEL_PROVIDER_OLLAMA
from ..protocol import make_error, make_response
from ..providers import ProviderChatRequest, resolve_provider_adapter
from .base import ProtocolNode, cap


class OllamaModelNode(ProtocolNode):
    node_id = "node.model.ollama"
    priority = 165

    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        source_env = ctx.env if ctx.env is not None else os.environ
        self.adapter = resolve_provider_adapter(MODEL_PROVIDER_OLLAMA, source_env)

    def capabilities(self) -> List:
        return [
            cap(
                name="model.chat.complete",
                description="Complete chat using Ollama provider",
                input_schema={"type": "object", "required": ["prompt"]},
                risk_class="read",
                required_extensions=[],
                approval_required=False,
                examples=["summarize this spec"],
                idempotency="idempotent",
                side_effect_scope="external",
                provider=MODEL_PROVIDER_OLLAMA,
            ),
            cap(
                name="model.chat.stream",
                description="Stream chat using Ollama provider",
                input_schema={"type": "object", "required": ["prompt"]},
                risk_class="read",
                required_extensions=[],
                approval_required=False,
                examples=["stream response"],
                idempotency="idempotent",
                side_effect_scope="external",
                provider=MODEL_PROVIDER_OLLAMA,
            ),
            cap(
                name="model.catalog.list",
                description="List Ollama models",
                input_schema={"type": "object"},
                risk_class="read",
                required_extensions=[],
                approval_required=False,
                examples=["list models"],
                idempotency="idempotent",
                side_effect_scope="external",
                provider=MODEL_PROVIDER_OLLAMA,
            ),
        ]

    @staticmethod
    def _llm_info(message: Dict[str, Any]) -> Dict[str, Any]:
        llm = (message.get("extensions", {}) or {}).get("llm", {})
        if not isinstance(llm, dict):
            llm = {}
        return llm

    @staticmethod
    def _messages_from_payload(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        raw = payload.get("messages", [])
        if not isinstance(raw, list):
            return []
        messages: List[Dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role", "")).strip().lower()
            content = item.get("content")
            if role not in {"system", "user", "assistant"}:
                continue
            if not isinstance(content, str) or not content.strip():
                continue
            messages.append({"role": role, "content": content})
        return messages

    def _catalog(self, parent_message_id: str | None) -> Dict[str, Any]:
        err = self.adapter.validate_catalog(parent_message_id)
        if err is not None:
            return err

        result = self.adapter.catalog(parent_message_id)
        return make_response(
            "model.catalog",
            {
                "provider": MODEL_PROVIDER_OLLAMA,
                "models": result.models,
                "fallback": result.fallback,
            },
            parent_message_id,
        )

    def handle(self, message: Dict[str, Any]) -> Dict[str, Any]:
        intent = message.get("intent")
        payload = message.get("payload", {})
        if not isinstance(payload, dict):
            return make_error("E_BAD_MESSAGE", "payload must be object", message.get("message_id"))

        llm = self._llm_info(message)
        model = str(llm.get("model", "")).strip()
        provider = str(llm.get("provider", "")).strip() or MODEL_PROVIDER_OLLAMA

        if payload.get("simulate_timeout"):
            return make_error(E_NODE_TIMEOUT, "Request timed out. You can retry.", message.get("message_id"), retryable=True)

        if intent == "model.catalog.list":
            return self._catalog(message.get("message_id"))

        if intent in {"model.chat.complete", "model.chat.stream"}:
            prompt = str(payload.get("prompt", "")).strip()
            messages = self._messages_from_payload(payload)
            if not messages and prompt:
                messages = [{"role": "user", "content": prompt}]
            if not messages:
                return make_error("E_BAD_MESSAGE", "prompt is required", message.get("message_id"))
            if not prompt:
                for item in reversed(messages):
                    if str(item.get("role", "")).strip().lower() == "user":
                        prompt = str(item.get("content", "")).strip()
                        if prompt:
                            break

            request_obj = ProviderChatRequest(
                model=model,
                prompt=prompt,
                llm=llm,
                parent_message_id=message.get("message_id"),
                messages=messages,
            )
            err = self.adapter.validate(request_obj)
            if err is not None:
                return err

            result, err = self.adapter.chat_completion(request_obj)
            if err is not None:
                return err
            assert result is not None

            response_intent = "model.chat.stream.chunk" if intent == "model.chat.stream" else "model.chat.completed"
            return make_response(
                response_intent,
                {
                    "provider": provider,
                    "model": model,
                    "text": result.text,
                    "tool_calls": result.tool_calls,
                },
                message.get("message_id"),
            )

        return make_error("E_NO_ROUTE", "Unsupported intent", message.get("message_id"))
