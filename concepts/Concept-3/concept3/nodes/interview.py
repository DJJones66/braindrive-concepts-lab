from __future__ import annotations

from typing import Any, Dict, List

from ..protocol import make_error, make_response, new_uuid, now_iso
from .base import ProtocolNode, cap
from .llm_driver import LLMSkillDriver


MIN_INTERVIEW_ANSWERS = 5


class InterviewWorkflowNode(ProtocolNode):
    node_id = "node.workflow.interview"
    priority = 140

    def capabilities(self) -> List:
        return [
            cap(
                name="workflow.interview.start",
                description="Start interview flow",
                input_schema={"type": "object"},
                risk_class="read",
                required_extensions=[],
                approval_required=False,
                examples=["start interview"],
                idempotency="non_idempotent",
                side_effect_scope="file",
            ),
            cap(
                name="workflow.interview.continue",
                description="Continue interview flow",
                input_schema={"type": "object", "required": ["answer"]},
                risk_class="read",
                required_extensions=[],
                approval_required=False,
                examples=["my answer is ..."],
                idempotency="non_idempotent",
                side_effect_scope="file",
            ),
            cap(
                name="workflow.interview.complete",
                description="Complete interview and summarize",
                input_schema={"type": "object"},
                risk_class="read",
                required_extensions=[],
                approval_required=False,
                examples=["finish interview"],
                idempotency="non_idempotent",
                side_effect_scope="file",
            ),
        ]

    @staticmethod
    def _llm_ext(message: Dict[str, Any]) -> Dict[str, Any]:
        extensions = message.get("extensions", {})
        if not isinstance(extensions, dict):
            return {}
        llm = extensions.get("llm", {})
        return llm if isinstance(llm, dict) else {}

    def _active_folder(self) -> str:
        if self.ctx.workflow_state is None:
            return ""
        return str(self.ctx.workflow_state.read("active_folder", ""))

    def _load_interview(self, folder: str) -> Dict[str, Any]:
        if self.ctx.workflow_state is None:
            return {"status": "idle", "answers": [], "question_index": 0}

        state = self.ctx.workflow_state.get()
        interviews = state.get("interviews", {})
        if not isinstance(interviews, dict):
            return {"status": "idle", "answers": [], "question_index": 0}

        item = interviews.get(folder)
        if not isinstance(item, dict):
            return {"status": "idle", "answers": [], "question_index": 0, "asked_questions": []}
        return item

    def _save_interview(self, folder: str, interview: Dict[str, Any]) -> None:
        if self.ctx.workflow_state is None:
            return

        def _mutate(state: Dict[str, Any]) -> None:
            interviews = state.setdefault("interviews", {})
            if not isinstance(interviews, dict):
                state["interviews"] = {}
                interviews = state["interviews"]
            interviews[folder] = interview

        self.ctx.workflow_state.mutate(_mutate)

    def _append_interview_session_history(self, folder: str, interview: Dict[str, Any], summary: str) -> None:
        log_path = self.ctx.library_root / folder / "interview.md"
        if not log_path.parent.exists():
            log_path.parent.mkdir(parents=True, exist_ok=True)

        existing = ""
        if log_path.exists() and log_path.is_file():
            existing = log_path.read_text(encoding="utf-8")

        answers = interview.get("answers", [])
        if not isinstance(answers, list):
            answers = []
        session_id = str(interview.get("session_id", "")).strip() or str(new_uuid())
        started_at = str(interview.get("started_at", "")).strip() or now_iso()
        completed_at = str(interview.get("completed_at", "")).strip() or now_iso()
        folder_title = folder.replace("-", " ")

        lines: List[str] = []
        if not existing.strip():
            lines.append(f"# {folder_title} Interview History")
            lines.append("")

        lines.append(f"## Interview Session {completed_at}")
        lines.append("")
        lines.append(f"- Session ID: `{session_id}`")
        lines.append(f"- Started: `{started_at}`")
        lines.append(f"- Completed: `{completed_at}`")
        lines.append(f"- Answers Collected: `{len([item for item in answers if isinstance(item, dict)])}`")
        lines.append("")
        lines.append("### Questions and Answers")
        lines.append("")
        if answers:
            for index, item in enumerate(answers, start=1):
                if not isinstance(item, dict):
                    continue
                question = str(item.get("question", "")).strip() or "(question unavailable)"
                answer = str(item.get("answer", "")).strip() or "(answer unavailable)"
                lines.append(f"{index}. Q: {question}")
                lines.append(f"   A: {answer}")
        else:
            lines.append("- No answers captured.")
        lines.append("")
        lines.append("### Summary")
        lines.append("")
        lines.append(summary.strip() or "_No summary generated._")
        lines.append("")
        lines.append("---")
        lines.append("")

        with log_path.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(lines))

    def _save_interview_history_entry(self, folder: str, interview: Dict[str, Any]) -> None:
        if self.ctx.workflow_state is None:
            return
        session = {
            "session_id": str(interview.get("session_id", "")).strip(),
            "started_at": str(interview.get("started_at", "")).strip(),
            "completed_at": str(interview.get("completed_at", "")).strip(),
            "answers": interview.get("answers", []),
            "summary": str(interview.get("summary", "")).strip(),
        }

        def _mutate(state: Dict[str, Any]) -> None:
            history = state.setdefault("interview_history", {})
            if not isinstance(history, dict):
                state["interview_history"] = {}
                history = state["interview_history"]
            entries = history.setdefault(folder, [])
            if not isinstance(entries, list):
                history[folder] = []
                entries = history[folder]
            entries.append(session)

        self.ctx.workflow_state.mutate(_mutate)

    @staticmethod
    def _first_question_line(text: str) -> str:
        for line in text.splitlines():
            cleaned = line.strip(" -\t")
            if cleaned:
                return cleaned
        return text.strip()

    def _build_next_question_prompt(self, *, folder: str, skill: str, answers: List[Dict[str, Any]]) -> str:
        transcript = "\n".join(
            [f"Q: {item.get('question', '').strip()}\nA: {item.get('answer', '').strip()}" for item in answers]
        ).strip()
        if not transcript:
            transcript = "No prior Q/A."

        return (
            f"{skill}\n\n"
            f"You are conducting a requirements interview for folder '{folder}'.\n"
            "Ask exactly one concise next question based on prior answers.\n"
            "Do not repeat earlier questions. Avoid meta commentary.\n"
            "Return only the question text.\n\n"
            f"Prior Q/A:\n{transcript}\n"
        )

    def _build_completion_prompt(self, *, folder: str, skill: str, answers: List[Dict[str, Any]]) -> str:
        transcript = "\n".join(
            [f"- Q: {item.get('question', '').strip()} | A: {item.get('answer', '').strip()}" for item in answers]
        )
        return (
            f"{skill}\n\n"
            f"Summarize this completed interview for folder '{folder}'.\n"
            "Output markdown only with these sections:\n"
            "## Goal\n## Success Criteria\n## Current State\n## Risks\n## First Milestone\n"
            "Normalize rough phrasing into concise, clear statements.\n\n"
            f"Interview transcript:\n{transcript}\n"
        )

    def handle(self, message: Dict[str, Any]) -> Dict[str, Any]:
        intent = message.get("intent")
        payload = message.get("payload", {})
        if not isinstance(payload, dict):
            return make_error("E_BAD_MESSAGE", "payload must be object", message.get("message_id"))

        folder = self._active_folder()
        if not folder:
            return make_error("E_NODE_ERROR", "No active folder selected", message.get("message_id"))

        interview = self._load_interview(folder)
        driver = LLMSkillDriver(self.ctx)
        llm_ext = self._llm_ext(message)
        skill, skill_err = driver.load_skill("interview.md", message.get("message_id"))
        if skill_err:
            return skill_err
        assert skill is not None

        if intent == "workflow.interview.start":
            interview = {
                "status": "in_progress",
                "answers": [],
                "question_index": 0,
                "asked_questions": [],
                "session_id": str(new_uuid()),
                "started_at": now_iso(),
            }
            prompt = self._build_next_question_prompt(folder=folder, skill=skill, answers=[])
            generated_question, err = driver.complete(
                prompt=prompt,
                parent_message_id=message.get("message_id"),
                llm_ext=llm_ext,
            )
            if err:
                return err

            question = self._first_question_line(generated_question or "")
            if not question:
                return make_error("E_NODE_ERROR", "Model did not return interview question", message.get("message_id"))

            interview["asked_questions"] = [question]
            self._save_interview(folder, interview)
            return make_response(
                "workflow.interview.question",
                {
                    "folder": folder,
                    "question_index": 0,
                    "question": question,
                },
                message.get("message_id"),
            )

        if intent == "workflow.interview.continue":
            if interview.get("status") != "in_progress":
                return make_error("E_NODE_ERROR", "Interview not started", message.get("message_id"))

            answer = str(payload.get("answer", "")).strip()
            if not answer:
                return make_error("E_BAD_MESSAGE", "answer is required", message.get("message_id"))

            answers = interview.setdefault("answers", [])
            if not isinstance(answers, list):
                answers = []
                interview["answers"] = answers
            asked_questions = interview.setdefault("asked_questions", [])
            if not isinstance(asked_questions, list):
                asked_questions = []
                interview["asked_questions"] = asked_questions
            current_question = str(asked_questions[-1]).strip() if asked_questions else ""
            answers.append({"question": current_question, "answer": answer})
            interview["question_index"] = len(answers)

            if len(answers) >= MIN_INTERVIEW_ANSWERS:
                interview["status"] = "ready_to_complete"
                self._save_interview(folder, interview)
                return make_response(
                    "workflow.interview.ready",
                    {
                        "folder": folder,
                        "answers_collected": len(answers),
                        "next": "workflow.interview.complete",
                    },
                    message.get("message_id"),
                )

            prompt = self._build_next_question_prompt(folder=folder, skill=skill, answers=answers)
            generated_question, err = driver.complete(
                prompt=prompt,
                parent_message_id=message.get("message_id"),
                llm_ext=llm_ext,
            )
            if err:
                return err

            question = self._first_question_line(generated_question or "")
            if not question:
                return make_error("E_NODE_ERROR", "Model did not return interview question", message.get("message_id"))
            asked_questions.append(question)
            self._save_interview(folder, interview)
            return make_response(
                "workflow.interview.question",
                {
                    "folder": folder,
                    "question_index": int(interview.get("question_index", 0)),
                    "question": question,
                    "answers_collected": len(answers),
                },
                message.get("message_id"),
            )

        if intent == "workflow.interview.complete":
            if interview.get("status") not in {"in_progress", "ready_to_complete"}:
                return make_error("E_NODE_ERROR", "Interview not started", message.get("message_id"))

            answers = interview.get("answers", [])
            if not isinstance(answers, list) or not answers:
                return make_error("E_NODE_ERROR", "Interview has no answers", message.get("message_id"))

            prompt = self._build_completion_prompt(
                folder=folder,
                skill=skill,
                answers=[item for item in answers if isinstance(item, dict)],
            )
            summary, err = driver.complete(
                prompt=prompt,
                parent_message_id=message.get("message_id"),
                llm_ext=llm_ext,
            )
            if err:
                return err

            interview["status"] = "completed"
            interview["summary"] = summary or ""
            interview["completed_at"] = now_iso()

            try:
                self._append_interview_session_history(folder, interview, summary or "")
            except OSError as exc:
                return make_error("E_NODE_ERROR", f"Failed to persist interview history: {exc}", message.get("message_id"))

            self._save_interview(folder, interview)
            self._save_interview_history_entry(folder, interview)

            return make_response(
                "workflow.interview.completed",
                {
                    "folder": folder,
                    "answers_collected": len([item for item in answers if isinstance(item, dict)]),
                    "summary": summary or "",
                    "history_path": f"{folder}/interview.md",
                    "session_id": str(interview.get("session_id", "")),
                    "completed_at": str(interview.get("completed_at", "")),
                },
                message.get("message_id"),
            )

        return make_error("E_NO_ROUTE", "Unsupported intent", message.get("message_id"))
