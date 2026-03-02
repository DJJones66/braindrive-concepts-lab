#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from braindrive_runtime.protocol import new_uuid
from braindrive_runtime.runtime import BrainDriveRuntime


def pretty(title: str, payload) -> None:
    print(f"\n== {title} ==")
    print(json.dumps(payload, indent=2, ensure_ascii=True))


def main() -> None:
    root = ROOT
    library = root / "demo-library"
    data = root / "demo-data"

    runtime = BrainDriveRuntime(
        library_root=library,
        data_root=data,
        env={
            "BRAINDRIVE_ENABLE_TEST_ENDPOINTS": "true",
            "BRAINDRIVE_DEFAULT_PROVIDER": "ollama",
            "BRAINDRIVE_OLLAMA_BASE_URL": "http://localhost:11434/v1",
            "BRAINDRIVE_OLLAMA_DEFAULT_MODEL": "llama3:8b",
        },
    )

    pretty("bootstrap", runtime.bootstrap())

    create_folder = runtime.route(
        {
            "protocol_version": "0.1",
            "message_id": new_uuid(),
            "intent": "folder.create",
            "payload": {"topic": "Finances"},
            "extensions": {
                "confirmation": {
                    "required": True,
                    "status": "approved",
                    "request_id": "appr-demo-folder",
                }
            },
        }
    )
    pretty("folder.create", create_folder)

    switch_folder = runtime.route(
        {
            "protocol_version": "0.1",
            "message_id": new_uuid(),
            "intent": "folder.switch",
            "payload": {"folder": "finances"},
        }
    )
    pretty("folder.switch", switch_folder)

    interview_start = runtime.route(
        {
            "protocol_version": "0.1",
            "message_id": new_uuid(),
            "intent": "workflow.interview.start",
            "payload": {},
        }
    )
    pretty("interview.start", interview_start)

    for answer in [
        "Become debt free and build emergency fund",
        "6 months of expenses saved",
        "Current savings are low",
        "Income variability",
        "Automate monthly savings",
    ]:
        response = runtime.route(
            {
                "protocol_version": "0.1",
                "message_id": new_uuid(),
                "intent": "workflow.interview.continue",
                "payload": {"answer": answer},
            }
        )
        pretty("interview.continue", response)

    spec_generate = runtime.route(
        {
            "protocol_version": "0.1",
            "message_id": new_uuid(),
            "intent": "workflow.spec.generate",
            "payload": {},
        }
    )
    pretty("spec.generate", spec_generate)

    spec_proposal = runtime.route(
        {
            "protocol_version": "0.1",
            "message_id": new_uuid(),
            "intent": "workflow.spec.propose_save",
            "payload": {},
        }
    )
    pretty("spec.propose_save", spec_proposal)

    approval_result = runtime.apply_approval_flow(spec_proposal.get("payload", {}), approve=True)
    pretty("approval+write+commit", approval_result)

    plan_generate = runtime.route(
        {
            "protocol_version": "0.1",
            "message_id": new_uuid(),
            "intent": "workflow.plan.generate",
            "payload": {},
        }
    )
    pretty("plan.generate", plan_generate)

    model_call = runtime.route(
        {
            "protocol_version": "0.1",
            "message_id": new_uuid(),
            "intent": "model.chat.complete",
            "payload": {"prompt": "Summarize next action"},
            "extensions": {"llm": {"provider": "ollama", "model": "llama3:8b"}},
        }
    )
    pretty("model.chat.complete", model_call)


if __name__ == "__main__":
    main()
