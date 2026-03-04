from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional

from .protocol import http_post_json


def start_registration_loop(
    descriptor: Dict[str, Any],
    register_url: str,
    heartbeat_url: str,
    *,
    heartbeat_sec: float = 5.0,
    register_retry_sec: float = 2.0,
    stop_event: Optional[threading.Event] = None,
) -> threading.Thread:
    state = {"lease_token": None}
    local_stop = stop_event or threading.Event()

    def _loop() -> None:
        while not local_stop.is_set():
            try:
                if not state["lease_token"]:
                    reg_resp = http_post_json(register_url, descriptor, timeout_sec=2.5)
                    state["lease_token"] = reg_resp.get("lease_token")
                    if not state["lease_token"]:
                        raise RuntimeError("register response missing lease_token")
                    print(f"[{descriptor['node_id']}] registered with router")

                hb_resp = http_post_json(
                    heartbeat_url,
                    {
                        "node_id": descriptor["node_id"],
                        "lease_token": state["lease_token"],
                    },
                    timeout_sec=2.5,
                )
                if not hb_resp.get("ok", False):
                    raise RuntimeError("heartbeat rejected")
                time.sleep(heartbeat_sec)
            except Exception as exc:
                state["lease_token"] = None
                print(f"[{descriptor['node_id']}] registration/heartbeat issue: {exc}")
                time.sleep(register_retry_sec)

    thread = threading.Thread(target=_loop, daemon=True, name=f"register-{descriptor['node_id']}")
    thread.start()
    return thread
