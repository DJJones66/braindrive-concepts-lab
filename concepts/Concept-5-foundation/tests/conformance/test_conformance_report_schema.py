from __future__ import annotations

import json
from pathlib import Path

from tests.conformance.report_schema import validate_conformance_report


def test_conformance_report_matches_expected_schema(tmp_path: Path):
    report = {
        "suite": "D147-style config-only swap conformance",
        "scenarios": [
            {"scenario": "provider_swap", "status": "pass", "detail": "ok"},
            {"scenario": "model_swap", "status": "pass", "detail": "ok"},
        ],
    }

    target = tmp_path / "conformance-report.json"
    target.write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8")

    loaded = json.loads(target.read_text(encoding="utf-8"))
    errors = validate_conformance_report(loaded)
    assert errors == []
