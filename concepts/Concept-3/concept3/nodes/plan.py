from __future__ import annotations

from typing import Any, Dict, List

from ..protocol import make_error, make_response, new_uuid, now_iso
from .base import ProtocolNode, cap
from .llm_driver import LLMSkillDriver


class PlanWorkflowNode(ProtocolNode):
    node_id = "node.workflow.plan"
    priority = 140

    def capabilities(self) -> List:
        return [
            cap(
                name="workflow.plan.generate",
                description="Generate plan markdown from spec",
                input_schema={"type": "object"},
                risk_class="read",
                required_extensions=[],
                approval_required=False,
                examples=["generate plan"],
                idempotency="idempotent",
                side_effect_scope="none",
            ),
            cap(
                name="workflow.plan.propose_save",
                description="Create approval payload for saving plan.md",
                input_schema={"type": "object"},
                risk_class="mutate",
                required_extensions=[],
                approval_required=False,
                examples=["propose plan save"],
                idempotency="non_idempotent",
                side_effect_scope="none",
            ),
        ]

    def _active_folder(self) -> str:
        if self.ctx.workflow_state is None:
            return ""
        return str(self.ctx.workflow_state.read("active_folder", ""))

    def _spec_text(self, folder: str) -> str:
        path = self.ctx.library_root / folder / "spec.md"
        if not path.exists() or not path.is_file():
            if self.ctx.workflow_state is None:
                return ""
            generated = self.ctx.workflow_state.read("generated_specs", {})
            if not isinstance(generated, dict):
                return ""
            value = generated.get(folder, "")
            return str(value) if value is not None else ""
        return path.read_text(encoding="utf-8")

    @staticmethod
    def _llm_ext(message: Dict[str, Any]) -> Dict[str, Any]:
        extensions = message.get("extensions", {})
        if not isinstance(extensions, dict):
            return {}
        llm = extensions.get("llm", {})
        return llm if isinstance(llm, dict) else {}

    def _build_plan_prompt(self, *, folder: str, skill: str, spec_text: str) -> str:
        source_spec = spec_text.strip() or "No spec.md exists yet."
        return (
            f"{skill}\n\n"
            f"Generate a practical markdown build plan for folder '{folder}'.\n"
            "Output markdown only (no code fences).\n"
            "Include:\n"
            "# <Folder> Plan\n"
            "## Phase 1: Clarify\n## Phase 2: Execute\n## Phase 3: Validate\n"
            "Each phase must include actionable bullet points.\n"
            "Ground the plan in the provided spec.\n\n"
            f"Spec source:\n{source_spec}\n"
        )

    def _generate_plan_markdown(
        self,
        *,
        message: Dict[str, Any],
        folder: str,
        spec_text: str,
    ) -> tuple[str | None, Dict[str, Any] | None]:
        driver = LLMSkillDriver(self.ctx)
        skill, err = driver.load_skill("plan-generation.md", message.get("message_id"))
        if err:
            return None, err
        assert skill is not None

        prompt = self._build_plan_prompt(folder=folder, skill=skill, spec_text=spec_text)
        return driver.complete(
            prompt=prompt,
            parent_message_id=message.get("message_id"),
            llm_ext=self._llm_ext(message),
        )

    def handle(self, message: Dict[str, Any]) -> Dict[str, Any]:
        intent = message.get("intent")
        payload = message.get("payload", {})
        if not isinstance(payload, dict):
            return make_error("E_BAD_MESSAGE", "payload must be object", message.get("message_id"))

        folder = self._active_folder()
        if not folder:
            return make_error("E_NODE_ERROR", "No active folder selected", message.get("message_id"))

        spec_text = self._spec_text(folder)

        if intent == "workflow.plan.generate":
            generated, err = self._generate_plan_markdown(
                message=message,
                folder=folder,
                spec_text=spec_text,
            )
            if err:
                return err
            assert generated is not None
            return make_response(
                "workflow.plan.generated",
                {
                    "folder": folder,
                    "grounded_in_spec": bool(spec_text.strip()),
                    "plan_markdown": generated,
                },
                message.get("message_id"),
            )

        if intent == "workflow.plan.propose_save":
            plan_markdown = str(payload.get("plan_markdown", "")).strip()
            if not plan_markdown:
                generated, err = self._generate_plan_markdown(
                    message=message,
                    folder=folder,
                    spec_text=spec_text,
                )
                if err:
                    return err
                assert generated is not None
                plan_markdown = generated
            request_id = f"appr-{new_uuid()}"
            change = {
                "path": f"{folder}/plan.md",
                "operation": "write",
                "summary": "Save generated plan",
                "diff_preview": plan_markdown[:500],
            }
            return make_response(
                "approval.request",
                {
                    "request_id": request_id,
                    "intent_being_guarded": "memory.write.propose",
                    "changes": [change],
                    "expires_at": now_iso(),
                    "proposed_write": {
                        "path": f"{folder}/plan.md",
                        "content": plan_markdown,
                    },
                },
                message.get("message_id"),
            )

        return make_error("E_NO_ROUTE", "Unsupported intent", message.get("message_id"))
