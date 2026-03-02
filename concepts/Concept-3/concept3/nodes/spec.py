from __future__ import annotations

from typing import Any, Dict, List

from ..protocol import make_error, make_response, new_uuid, now_iso
from .base import ProtocolNode, cap
from .llm_driver import LLMSkillDriver, read_context_doc


class SpecWorkflowNode(ProtocolNode):
    node_id = "node.workflow.spec"
    priority = 140

    def capabilities(self) -> List:
        return [
            cap(
                name="workflow.spec.generate",
                description="Generate spec markdown from interview state",
                input_schema={"type": "object"},
                risk_class="read",
                required_extensions=[],
                approval_required=False,
                examples=["generate spec"],
                idempotency="idempotent",
                side_effect_scope="none",
            ),
            cap(
                name="workflow.spec.propose_save",
                description="Create approval payload for saving spec.md",
                input_schema={"type": "object"},
                risk_class="mutate",
                required_extensions=[],
                approval_required=False,
                examples=["propose spec save"],
                idempotency="non_idempotent",
                side_effect_scope="none",
            ),
        ]

    def _active_folder(self) -> str:
        if self.ctx.workflow_state is None:
            return ""
        return str(self.ctx.workflow_state.read("active_folder", ""))

    def _interview_answers(self, folder: str) -> List[Dict[str, Any]]:
        if self.ctx.workflow_state is None:
            return []
        interviews = self.ctx.workflow_state.read("interviews", {})
        if not isinstance(interviews, dict):
            return []
        interview = interviews.get(folder)
        if not isinstance(interview, dict):
            return []
        answers = interview.get("answers", [])
        return [item for item in answers if isinstance(item, dict)] if isinstance(answers, list) else []

    def _interview_summary(self, folder: str) -> str:
        if self.ctx.workflow_state is None:
            return ""
        interviews = self.ctx.workflow_state.read("interviews", {})
        if not isinstance(interviews, dict):
            return ""
        interview = interviews.get(folder)
        if not isinstance(interview, dict):
            return ""
        value = interview.get("summary", "")
        return str(value).strip() if value is not None else ""

    def _interview_history_markdown(self, folder: str) -> str:
        path = self.ctx.library_root / folder / "interview.md"
        if not path.exists() or not path.is_file():
            return ""
        text = path.read_text(encoding="utf-8")
        if len(text) <= self.MAX_INTERVIEW_HISTORY_CHARS:
            return text
        return (
            f"(Truncated to most recent {self.MAX_INTERVIEW_HISTORY_CHARS} characters)\n\n"
            + text[-self.MAX_INTERVIEW_HISTORY_CHARS :]
        )

    def _save_generated_spec(self, folder: str, markdown: str) -> None:
        if self.ctx.workflow_state is None:
            return

        def _mutate(state: Dict[str, Any]) -> None:
            generated = state.setdefault("generated_specs", {})
            if not isinstance(generated, dict):
                state["generated_specs"] = {}
                generated = state["generated_specs"]
            generated[folder] = markdown

        self.ctx.workflow_state.mutate(_mutate)

    @staticmethod
    def _llm_ext(message: Dict[str, Any]) -> Dict[str, Any]:
        extensions = message.get("extensions", {})
        if not isinstance(extensions, dict):
            return {}
        llm = extensions.get("llm", {})
        return llm if isinstance(llm, dict) else {}

    def _build_spec_prompt(self, *, folder: str, skill: str, answers: List[Dict[str, Any]], summary: str) -> str:
        answers_block = "\n".join(
            [f"- Q: {item.get('question', '').strip()} | A: {item.get('answer', '').strip()}" for item in answers]
        ).strip()
        if not answers_block:
            answers_block = "- No interview answers available."

        agent_text = read_context_doc(self.ctx.library_root / folder / "AGENT.md")
        if not agent_text:
            agent_text = "No AGENT.md context available."
        if not summary:
            summary = "No interview summary available."
        history_markdown = self._interview_history_markdown(folder)
        if not history_markdown:
            history_markdown = "No interview.md history available."

        return (
            f"{skill}\n\n"
            f"Generate a high-quality markdown spec for folder '{folder}'.\n"
            "Output markdown only (no code fences).\n"
            "Include sections:\n"
            "# <Folder> Spec\n"
            "## Goal\n## Success Criteria\n## Current State\n## Risks\n## First Milestone\n## Scope\n## Non-Goals\n## Open Questions\n"
            "Rewrite rough notes into clear professional language while preserving intent.\n\n"
            f"Folder context (AGENT.md):\n{agent_text}\n\n"
            f"Interview history (interview.md):\n{history_markdown}\n\n"
            f"Interview summary:\n{summary}\n\n"
            f"Interview Q/A evidence:\n{answers_block}\n"
        )

    def _generate_spec_markdown(
        self,
        *,
        message: Dict[str, Any],
        folder: str,
        answers: List[Dict[str, Any]],
    ) -> tuple[str | None, Dict[str, Any] | None]:
        driver = LLMSkillDriver(self.ctx)
        skill, err = driver.load_skill("spec-generation.md", message.get("message_id"))
        if err:
            return None, err
        assert skill is not None

        prompt = self._build_spec_prompt(
            folder=folder,
            skill=skill,
            answers=answers,
            summary=self._interview_summary(folder),
        )
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

        answers = self._interview_answers(folder)

        if intent == "workflow.spec.generate":
            generated, err = self._generate_spec_markdown(message=message, folder=folder, answers=answers)
            if err:
                return err
            assert generated is not None
            self._save_generated_spec(folder, generated)
            return make_response(
                "workflow.spec.generated",
                {
                    "folder": folder,
                    "spec_markdown": generated,
                },
                message.get("message_id"),
            )

        if intent == "workflow.spec.propose_save":
            spec_markdown = str(payload.get("spec_markdown", "")).strip()
            if not spec_markdown:
                generated, err = self._generate_spec_markdown(message=message, folder=folder, answers=answers)
                if err:
                    return err
                assert generated is not None
                spec_markdown = generated
            self._save_generated_spec(folder, spec_markdown)
            request_id = f"appr-{new_uuid()}"
            change = {
                "path": f"{folder}/spec.md",
                "operation": "write",
                "summary": "Save generated spec",
                "diff_preview": spec_markdown[:500],
            }
            return make_response(
                "approval.request",
                {
                    "request_id": request_id,
                    "intent_being_guarded": "memory.write.propose",
                    "changes": [change],
                    "expires_at": now_iso(),
                    "proposed_write": {
                        "path": f"{folder}/spec.md",
                        "content": spec_markdown,
                    },
                },
                message.get("message_id"),
            )

        return make_error("E_NO_ROUTE", "Unsupported intent", message.get("message_id"))
    MAX_INTERVIEW_HISTORY_CHARS = 24000
