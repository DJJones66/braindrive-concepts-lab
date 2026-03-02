from __future__ import annotations

import json
import os
import socket
from typing import Any, Dict, List
from urllib import error, request

from ..constants import E_NODE_ERROR, E_NODE_TIMEOUT, E_NODE_UNAVAILABLE, MODEL_PROVIDER_OLLAMA
from ..protocol import make_error, make_response
from .base import ProtocolNode, cap


class OllamaModelNode(ProtocolNode):
    node_id = "node.model.ollama"
    priority = 165
    _FALLBACK_MODELS = [
        "llama3:8b",
        "mistral:7b",
        "phi3:mini",
    ]

    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        source_env = ctx.env if ctx.env is not None else os.environ
        self.base_url = str(source_env.get("BRAINDRIVE_OLLAMA_BASE_URL", "")).rstrip("/")
        self.api_key = str(source_env.get("BRAINDRIVE_OLLAMA_API_KEY", "")).strip()
        self.timeout_sec = self._parse_timeout(str(source_env.get("BRAINDRIVE_MODEL_TIMEOUT_SEC", "30")))

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
    def _parse_timeout(raw: str) -> float:
        try:
            return max(1.0, float(raw))
        except (TypeError, ValueError):
            return 30.0

    def _llm_info(self, message: Dict[str, Any]) -> Dict[str, Any]:
        llm = (message.get("extensions", {}) or {}).get("llm", {})
        if not isinstance(llm, dict):
            llm = {}
        return llm

    def _endpoint(self, path: str) -> str:
        clean = path.lstrip("/")
        return f"{self.base_url}/{clean}" if self.base_url else clean

    @staticmethod
    def _looks_like_timeout(reason: Any) -> bool:
        if isinstance(reason, (TimeoutError, socket.timeout)):
            return True
        return "timed out" in str(reason).lower()

    def _build_headers(self) -> Dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _request_json(
        self,
        *,
        method: str,
        path: str,
        parent_message_id: str | None,
        payload: Dict[str, Any] | None = None,
    ) -> tuple[Dict[str, Any] | None, Dict[str, Any] | None]:
        body = None
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=True).encode("utf-8")

        req = request.Request(
            url=self._endpoint(path),
            data=body,
            headers=self._build_headers(),
            method=method,
        )
        try:
            with request.urlopen(req, timeout=self.timeout_sec) as resp:
                raw = resp.read()
        except error.HTTPError as exc:
            error_excerpt = ""
            try:
                error_excerpt = exc.read().decode("utf-8", errors="replace")[:320]
            except Exception:
                error_excerpt = ""

            if exc.code in {401, 403}:
                return None, make_error(
                    E_NODE_UNAVAILABLE,
                    "Ollama authentication failed. Check BRAINDRIVE_OLLAMA_API_KEY.",
                    parent_message_id,
                    retryable=False,
                    details={"status": exc.code, "upstream": error_excerpt},
                )
            if exc.code == 404:
                return None, make_error(
                    E_NODE_UNAVAILABLE,
                    "Ollama endpoint not found. Check BRAINDRIVE_OLLAMA_BASE_URL includes /v1.",
                    parent_message_id,
                    retryable=False,
                    details={"status": exc.code, "upstream": error_excerpt},
                )
            if exc.code in {408, 409, 429, 500, 502, 503, 504}:
                return None, make_error(
                    E_NODE_UNAVAILABLE,
                    f"Ollama request failed with HTTP {exc.code}. You can retry.",
                    parent_message_id,
                    retryable=True,
                    details={"status": exc.code, "upstream": error_excerpt},
                )
            return None, make_error(
                E_NODE_ERROR,
                f"Ollama request failed with HTTP {exc.code}.",
                parent_message_id,
                retryable=False,
                details={"status": exc.code, "upstream": error_excerpt},
            )
        except error.URLError as exc:
            if self._looks_like_timeout(exc.reason):
                return None, make_error(
                    E_NODE_TIMEOUT,
                    "Ollama request timed out. You can retry.",
                    parent_message_id,
                    retryable=True,
                    details={"reason": str(exc.reason)},
                )
            return None, make_error(
                E_NODE_UNAVAILABLE,
                "Ollama request failed. Check connectivity and BRAINDRIVE_OLLAMA_BASE_URL.",
                parent_message_id,
                retryable=True,
                details={"reason": str(exc.reason)},
            )
        except (TimeoutError, socket.timeout):
            return None, make_error(
                E_NODE_TIMEOUT,
                "Ollama request timed out. You can retry.",
                parent_message_id,
                retryable=True,
            )
        except Exception as exc:
            return None, make_error(
                E_NODE_ERROR,
                f"Ollama request failed: {type(exc).__name__}",
                parent_message_id,
                retryable=False,
                details={"error": str(exc)},
            )

        try:
            parsed = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return None, make_error(
                E_NODE_ERROR,
                "Ollama returned invalid JSON.",
                parent_message_id,
                retryable=False,
            )

        if not isinstance(parsed, dict):
            return None, make_error(
                E_NODE_ERROR,
                "Ollama response was not a JSON object.",
                parent_message_id,
                retryable=False,
            )
        return parsed, None

    @staticmethod
    def _content_to_text(content: Any) -> str:
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

    def _extract_completion_text(self, response_body: Dict[str, Any]) -> str:
        choices = response_body.get("choices")
        if not isinstance(choices, list) or not choices:
            return ""
        first = choices[0] if isinstance(choices[0], dict) else {}
        message = first.get("message") if isinstance(first.get("message"), dict) else {}
        text = self._content_to_text(message.get("content"))
        if text:
            return text
        delta = first.get("delta") if isinstance(first.get("delta"), dict) else {}
        return self._content_to_text(delta.get("content"))

    def _chat_completion(
        self,
        *,
        model: str,
        prompt: str,
        llm: Dict[str, Any],
        parent_message_id: str | None,
    ) -> tuple[str | None, Dict[str, Any] | None]:
        body: Dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
        }

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

        response_body, err = self._request_json(
            method="POST",
            path="/chat/completions",
            parent_message_id=parent_message_id,
            payload=body,
        )
        if err:
            return None, err

        text = self._extract_completion_text(response_body or {})
        if not text:
            return None, make_error(
                E_NODE_ERROR,
                "Ollama response did not include assistant text.",
                parent_message_id,
                retryable=False,
                details={"provider": MODEL_PROVIDER_OLLAMA},
            )
        return text, None

    def _catalog(self, parent_message_id: str | None) -> Dict[str, Any]:
        response_body, err = self._request_json(
            method="GET",
            path="/models",
            parent_message_id=parent_message_id,
            payload=None,
        )
        models: List[str] = []
        if response_body is not None:
            entries = response_body.get("data")
            if isinstance(entries, list):
                for item in entries:
                    if not isinstance(item, dict):
                        continue
                    model_id = item.get("id")
                    if isinstance(model_id, str) and model_id.strip():
                        models.append(model_id.strip())

        if not models:
            models = list(self._FALLBACK_MODELS)
        else:
            models = sorted(set(models))

        return make_response(
            "model.catalog",
            {
                "provider": MODEL_PROVIDER_OLLAMA,
                "models": models,
                "fallback": bool(err is not None),
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

        if not self.base_url:
            return make_error(
                E_NODE_UNAVAILABLE,
                "BRAINDRIVE_OLLAMA_BASE_URL is required for provider ollama",
                message.get("message_id"),
            )

        if intent == "model.catalog.list":
            return self._catalog(message.get("message_id"))

        if intent in {"model.chat.complete", "model.chat.stream"}:
            prompt = str(payload.get("prompt", "")).strip()
            if not prompt:
                return make_error("E_BAD_MESSAGE", "prompt is required", message.get("message_id"))
            text, err = self._chat_completion(
                model=model,
                prompt=prompt,
                llm=llm,
                parent_message_id=message.get("message_id"),
            )
            if err:
                return err
            assert text is not None
            response_intent = "model.chat.stream.chunk" if intent == "model.chat.stream" else "model.chat.completed"
            return make_response(
                response_intent,
                {
                    "provider": provider,
                    "model": model,
                    "text": text,
                },
                message.get("message_id"),
            )

        return make_error("E_NO_ROUTE", "Unsupported intent", message.get("message_id"))
