from __future__ import annotations

from typing import Any, Dict, List


REQUIRED_SCENARIO_FIELDS = {"scenario", "status", "detail"}


def validate_conformance_report(report: Dict[str, Any]) -> List[str]:
    errors: List[str] = []

    if not isinstance(report, dict):
        return ["report must be an object"]

    suite = report.get("suite")
    if not isinstance(suite, str) or not suite.strip():
        errors.append("suite must be a non-empty string")

    scenarios = report.get("scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        errors.append("scenarios must be a non-empty list")
        return errors

    for index, scenario in enumerate(scenarios):
        if not isinstance(scenario, dict):
            errors.append(f"scenarios[{index}] must be an object")
            continue
        missing = REQUIRED_SCENARIO_FIELDS - set(scenario.keys())
        if missing:
            errors.append(f"scenarios[{index}] missing fields: {sorted(missing)}")
            continue
        if str(scenario.get("status", "")).strip().lower() not in {"pass", "fail"}:
            errors.append(f"scenarios[{index}].status must be pass|fail")

    return errors
