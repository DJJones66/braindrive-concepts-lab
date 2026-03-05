from __future__ import annotations

import json
import socket
from typing import Any, Dict, List
from urllib import error, request

from ..constants import E_NODE_ERROR, E_NODE_TIMEOUT, E_NODE_UNAVAILABLE, MODEL_PROVIDER_OLLAMA
from ..protocol import make_error
from .base import ProviderAdapter, ProviderCatalogResult, ProviderChatRequest, ProviderChatResult
from .common import apply_generation_options, extract_choice_text_and_tools, parse_timeout


class OllamaAdapter(ProviderAdapter):
    provider_name = MODEL_PROVIDER_OLLAMA
    _FALLBACK_MODELS = [
        "llama3:8b",
        "mistral:7b",
        "phi3:mini",
    ]

    def __init__(self, *, base_url: str, api_key: str, timeout_sec: str) -> None:
        self.base_url = str(base_url).rstrip("/")
        self.api_key = str(api_key).strip()
        self.timeout_sec = parse_timeout(timeout_sec)

    def validate_catalog(self, parent_message_id: str | None) -> Dict[str, Any] | None:
        if not self.base_url:
            return make_error(
                E_NODE_UNAVAILABLE,
                "BRAINDRIVE_OLLAMA_BASE_URL is required for provider ollama",
                parent_message_id,
            )
        return None

    def validate(self, request_obj: ProviderChatRequest) -> Dict[str, Any] | None:
        catalog_err = self.validate_catalog(request_obj.parent_message_id)
        if catalog_err is not None:
            return catalog_err
        if not request_obj.model:
            return make_error(
                E_NODE_UNAVAILABLE,
                "Default model is required for provider ollama",
                request_obj.parent_message_id,
            )
        return None

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

    def chat_completion(self, request_obj: ProviderChatRequest) -> tuple[ProviderChatResult | None, Dict[str, Any] | None]:
        messages = request_obj.messages if isinstance(request_obj.messages, list) else []
        if not messages:
            messages = [{"role": "user", "content": request_obj.prompt}]
        body: Dict[str, Any] = {
            "model": request_obj.model,
            "messages": messages,
            "stream": False,
        }
        apply_generation_options(body, request_obj.llm)

        response_body, err = self._request_json(
            method="POST",
            path="/chat/completions",
            parent_message_id=request_obj.parent_message_id,
            payload=body,
        )
        if err:
            return None, err

        parsed = response_body or {}
        text, tool_calls = extract_choice_text_and_tools(parsed)
        if not text and not tool_calls:
            return None, make_error(
                E_NODE_ERROR,
                "Ollama response did not include assistant text.",
                request_obj.parent_message_id,
                retryable=False,
                details={"provider": MODEL_PROVIDER_OLLAMA},
            )
        return ProviderChatResult(text=text, tool_calls=tool_calls, raw=parsed), None

    def catalog(self, parent_message_id: str | None) -> ProviderCatalogResult:
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

        return ProviderCatalogResult(models=models, fallback=bool(err is not None))
