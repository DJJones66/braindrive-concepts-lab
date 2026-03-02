from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

from .protocol import now_iso

SENSITIVE_KEYS = {"api_key", "authorization", "token", "secret"}


class Persistence:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.logs_dir = self.root / "logs"
        self.state_dir = self.root / "state"
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def log_path(self, name: str) -> Path:
        return self.logs_dir / f"{name}.jsonl"

    def state_path(self, name: str) -> Path:
        return self.state_dir / f"{name}.json"

    def append_log(self, name: str, item: Dict[str, Any]) -> None:
        path = self.log_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(_scrub_sensitive(item), ensure_ascii=True) + "\n")

    def emit_event(self, channel: str, event_type: str, payload: Dict[str, Any]) -> None:
        self.append_log(
            channel,
            {
                "ts": now_iso(),
                "event_type": event_type,
                "payload": payload,
            },
        )

    def load_state(self, name: str, default: Any) -> Any:
        path = self.state_path(name)
        if not path.exists():
            return default
        try:
            raw = path.read_text(encoding="utf-8")
            return json.loads(raw)
        except (OSError, json.JSONDecodeError):
            return default

    def save_state(self, name: str, value: Any) -> None:
        path = self.state_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(_scrub_sensitive(value), ensure_ascii=True, indent=2), encoding="utf-8")
        os.replace(tmp, path)


def _scrub_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for key, item in value.items():
            lowered = key.lower()
            if any(token in lowered for token in SENSITIVE_KEYS):
                out[key] = "<redacted>"
            else:
                out[key] = _scrub_sensitive(item)
        return out
    if isinstance(value, list):
        return [_scrub_sensitive(item) for item in value]
    return value
