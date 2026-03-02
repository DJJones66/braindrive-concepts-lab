from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List

from ..protocol import make_error, make_response
from .base import ProtocolNode, cap

SKILL_TEMPLATES = {
    # Legacy markdown compatibility.
    "interview.md": "# Interview Skill\n\nAsk adaptive questions and collect structured answers.\n",
    "spec-generation.md": "# Spec Generation Skill\n\nGenerate a concise, actionable spec from interview outcomes.\n",
    "plan-generation.md": "# Plan Generation Skill\n\nGenerate a phased plan from spec.md.\n",
    "folder-scaffold.md": "# Folder Scaffold Skill\n\nCreate AGENT.md with clear domain context and operating rules.\n",
    # Unified SkillNode manifest layout.
    "interview/skill.yaml": (
        "skill_id: interview\n"
        "version: 1.0.0\n"
        "description: Requirements interview workflow\n"
        "actions:\n"
        "  start:\n"
        "    execution_tier: stateful\n"
        "    prompt_template: prompts/start.md\n"
        "  continue:\n"
        "    execution_tier: stateful\n"
        "    prompt_template: prompts/continue.md\n"
        "  complete:\n"
        "    execution_tier: stateful\n"
        "    prompt_template: prompts/complete.md\n"
    ),
    "interview/prompts/start.md": "# Interview Skill\n\nAsk adaptive questions and collect structured answers.\n",
    "interview/prompts/continue.md": "# Interview Skill\n\nAsk adaptive questions and collect structured answers.\n",
    "interview/prompts/complete.md": "# Interview Skill\n\nAsk adaptive questions and collect structured answers.\n",
    "spec-generation/skill.yaml": (
        "skill_id: spec-generation\n"
        "version: 1.0.0\n"
        "description: Spec generation workflow\n"
        "actions:\n"
        "  generate:\n"
        "    execution_tier: read\n"
        "    prompt_template: prompts/generate.md\n"
        "  propose_save:\n"
        "    execution_tier: stateful\n"
        "    prompt_template: prompts/propose-save.md\n"
    ),
    "spec-generation/prompts/generate.md": (
        "# Spec Generation Skill\n\nGenerate a concise, actionable spec from interview outcomes.\n"
    ),
    "spec-generation/prompts/propose-save.md": (
        "# Spec Generation Skill\n\nGenerate a concise, actionable spec from interview outcomes.\n"
    ),
    "plan-generation/skill.yaml": (
        "skill_id: plan-generation\n"
        "version: 1.0.0\n"
        "description: Build plan generation workflow\n"
        "actions:\n"
        "  generate:\n"
        "    execution_tier: read\n"
        "    prompt_template: prompts/generate.md\n"
        "  propose_save:\n"
        "    execution_tier: stateful\n"
        "    prompt_template: prompts/propose-save.md\n"
    ),
    "plan-generation/prompts/generate.md": "# Plan Generation Skill\n\nGenerate a phased plan from spec.md.\n",
    "plan-generation/prompts/propose-save.md": "# Plan Generation Skill\n\nGenerate a phased plan from spec.md.\n",
}


class RuntimeBootstrapNode(ProtocolNode):
    node_id = "node.runtime.bootstrap"
    priority = 150

    def capabilities(self) -> List:
        return [
            cap(
                name="system.bootstrap",
                description="Initialize runtime prerequisites and skill files",
                input_schema={"type": "object"},
                risk_class="mutate",
                required_extensions=[],
                approval_required=False,
                examples=["bootstrap system"],
                idempotency="idempotent",
                side_effect_scope="file",
            ),
            cap(
                name="system.health.check",
                description="Return runtime health",
                input_schema={"type": "object"},
                risk_class="read",
                required_extensions=[],
                approval_required=False,
                examples=["health check"],
                idempotency="idempotent",
                side_effect_scope="none",
            ),
        ]

    def _ensure_skills(self) -> Dict[str, List[str]]:
        skills_dir = self.ctx.library_root / ".braindrive" / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)

        created: List[str] = []
        existing: List[str] = []
        for filename, content in SKILL_TEMPLATES.items():
            target = skills_dir / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                existing.append(filename)
                continue
            target.write_text(content, encoding="utf-8")
            created.append(filename)
        return {"created": created, "existing": existing}

    def _is_writable(self, path: Path) -> bool:
        try:
            probe = path / ".bdp-write-probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return True
        except OSError:
            return False

    def handle(self, message: Dict[str, object]) -> Dict[str, object]:
        intent = message.get("intent")

        if intent == "system.health.check":
            return make_response(
                "system.health",
                {
                    "ok": True,
                    "library_root": str(self.ctx.library_root),
                    "skills_dir": str(self.ctx.library_root / ".braindrive" / "skills"),
                },
                message.get("message_id"),
            )

        if intent != "system.bootstrap":
            return make_error("E_NO_ROUTE", "Unsupported intent", message.get("message_id"))

        if not self.ctx.library_root.exists():
            return make_error("E_NODE_ERROR", "Library folder not found", message.get("message_id"))
        if not self.ctx.library_root.is_dir():
            return make_error("E_NODE_ERROR", "Library path is not a directory", message.get("message_id"))
        if not self._is_writable(self.ctx.library_root):
            return make_error("E_NODE_ERROR", "Library folder not writable", message.get("message_id"))

        skills = self._ensure_skills()

        return make_response(
            "system.bootstrap.ready",
            {
                "ready": True,
                "library_root": str(self.ctx.library_root),
                "skills": skills,
                "platform": os.name,
            },
            message.get("message_id"),
        )
