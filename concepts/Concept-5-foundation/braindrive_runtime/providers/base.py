from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class ProviderChatRequest:
    model: str
    prompt: str
    llm: Dict[str, Any]
    parent_message_id: str | None
    messages: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class ProviderChatResult:
    text: str
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ProviderCatalogResult:
    models: List[str]
    fallback: bool


class ProviderAdapter(ABC):
    provider_name: str

    @abstractmethod
    def validate_catalog(self, parent_message_id: str | None) -> Dict[str, Any] | None:
        """Return protocol error payload when provider catalog prerequisites are missing."""

    @abstractmethod
    def validate(self, request: ProviderChatRequest) -> Dict[str, Any] | None:
        """Return protocol error payload when provider prerequisites are missing."""

    @abstractmethod
    def chat_completion(self, request: ProviderChatRequest) -> tuple[ProviderChatResult | None, Dict[str, Any] | None]:
        """Return chat result or protocol error payload."""

    @abstractmethod
    def catalog(self, parent_message_id: str | None) -> ProviderCatalogResult:
        """Return provider model catalog with fallback flag when upstream is unavailable."""
