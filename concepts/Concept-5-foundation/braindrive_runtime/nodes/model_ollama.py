from __future__ import annotations

from ..constants import MODEL_PROVIDER_OLLAMA
from .model_provider import ModelProviderNode


class OllamaModelNode(ModelProviderNode):
    node_id = "node.model.ollama"
    priority = 165

    def __init__(self, ctx) -> None:
        super().__init__(
            ctx,
            provider=MODEL_PROVIDER_OLLAMA,
            node_id=self.node_id,
            priority=self.priority,
            label="Ollama",
        )

