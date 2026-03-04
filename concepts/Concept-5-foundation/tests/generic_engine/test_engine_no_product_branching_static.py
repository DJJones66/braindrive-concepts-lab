from __future__ import annotations

from pathlib import Path


def test_dispatch_orchestration_has_no_skill_id_product_branches():
    repo_root = Path(__file__).resolve().parents[2]
    source = (repo_root / "braindrive_runtime" / "nodes" / "skill.py").read_text(encoding="utf-8")

    start = source.index("def _dispatch_action(")
    end = source.index("def _execute_with_events(")
    segment = source[start:end]

    assert "if skill_id ==" not in segment
    assert "skill_id == \"interview\"" not in segment
    assert "skill_id == \"spec-generation\"" not in segment
    assert "skill_id == \"plan-generation\"" not in segment
