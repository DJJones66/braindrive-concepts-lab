from __future__ import annotations

import threading
from copy import deepcopy
from typing import Any, Dict

from .persistence import Persistence


class WorkflowState:
    def __init__(self, persistence: Persistence) -> None:
        self.persistence = persistence
        self._state: Dict[str, Any] = {}
        self._reload_from_disk()
        self.lock = threading.Lock()

    def _normalize(self, state: Dict[str, Any]) -> Dict[str, Any]:
        state.setdefault("active_folder", "")
        state.setdefault("interviews", {})
        state.setdefault("settings", {})
        return state

    def _reload_from_disk(self) -> None:
        loaded = self.persistence.load_state("workflow_state", {})
        self._state = self._normalize(loaded if isinstance(loaded, dict) else {})

    def get(self) -> Dict[str, Any]:
        with self.lock:
            self._reload_from_disk()
            return deepcopy(self._state)

    def read(self, key: str, default: Any = None) -> Any:
        with self.lock:
            self._reload_from_disk()
            return deepcopy(self._state.get(key, default))

    def update(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        with self.lock:
            self._reload_from_disk()
            self._state.update(deepcopy(patch))
            self.persistence.save_state("workflow_state", self._state)
            return deepcopy(self._state)

    def mutate(self, fn) -> Dict[str, Any]:
        with self.lock:
            self._reload_from_disk()
            fn(self._state)
            self.persistence.save_state("workflow_state", self._state)
            return deepcopy(self._state)
