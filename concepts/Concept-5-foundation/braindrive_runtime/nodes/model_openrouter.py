from __future__ import annotations

from ..constants import MODEL_PROVIDER_OPENROUTER
from .model_provider import ModelProviderNode


class OpenRouterModelNode(ModelProviderNode):
    node_id = "node.model.openrouter"
    priority = 170

    def __init__(self, ctx) -> None:
        super().__init__(
            ctx,
            provider=MODEL_PROVIDER_OPENROUTER,
            node_id=self.node_id,
            priority=self.priority,
            label="OpenRouter",
        )

