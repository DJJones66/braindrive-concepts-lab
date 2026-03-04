from __future__ import annotations

from tests.conformance.report_schema import validate_conformance_report


def test_conformance_report_validator_rejects_invalid_payload():
    bad_report = {
        "suite": "",
        "scenarios": [
            {"scenario": "provider_swap", "detail": "missing status"},
            {"scenario": "model_swap", "status": "unknown", "detail": "invalid status"},
        ],
    }

    errors = validate_conformance_report(bad_report)
    assert errors
    assert any("suite must be a non-empty string" in item for item in errors)
    assert any("missing fields" in item for item in errors)
    assert any("status must be pass|fail" in item for item in errors)
