from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

from ..config import ConfigResolver
from ..protocol import make_error, make_response, new_uuid, now_iso
from .base import ProtocolNode, cap
from .llm_driver import LLMSkillDriver, read_context_doc

MIN_INTERVIEW_ANSWERS = 5
MAX_INTERVIEW_HISTORY_CHARS = 24000

LEGACY_INTENT_MAP: Dict[str, Dict[str, str]] = {
    "workflow.interview.start": {"skill_id": "interview", "action": "start", "execution_tier": "stateful"},
    "workflow.interview.continue": {"skill_id": "interview", "action": "continue", "execution_tier": "stateful"},
    "workflow.interview.complete": {"skill_id": "interview", "action": "complete", "execution_tier": "stateful"},
    "workflow.spec.generate": {"skill_id": "spec-generation", "action": "generate", "execution_tier": "read"},
    "workflow.spec.propose_save": {"skill_id": "spec-generation", "action": "propose_save", "execution_tier": "stateful"},
    "workflow.plan.generate": {"skill_id": "plan-generation", "action": "generate", "execution_tier": "read"},
    "workflow.plan.propose_save": {"skill_id": "plan-generation", "action": "propose_save", "execution_tier": "stateful"},
}


class SkillWorkflowNode(ProtocolNode):
    node_id = "node.workflow.skill"
    priority = 140

    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        source_env = self.ctx.env if self.ctx.env is not None else {}
        user_config_path_raw = str(source_env.get("BRAINDRIVE_USER_CONFIG_PATH", "")).strip()
        user_config_path = Path(user_config_path_raw) if user_config_path_raw else None
        self._config = ConfigResolver(env=source_env, user_config_path=user_config_path)
        self._catalog_cache: Dict[str, Dict[str, Any]] = {}
        self._catalog_fingerprint: Tuple[Tuple[str, int, int], ...] = tuple()

    def capabilities(self) -> List:
        return [
            cap(
                name="skill.catalog.list",
                description="List available skill definitions",
                input_schema={"type": "object"},
                risk_class="read",
                required_extensions=[],
                approval_required=False,
                examples=["list skills"],
                idempotency="idempotent",
                side_effect_scope="none",
            ),
            cap(
                name="skill.execute.read",
                description="Execute read-only skill action",
                input_schema={"type": "object", "required": ["skill_id", "action"]},
                risk_class="read",
                required_extensions=[],
                approval_required=False,
                examples=["execute read skill"],
                idempotency="non_idempotent",
                side_effect_scope="none",
            ),
            cap(
                name="skill.execute.stateful",
                description="Execute stateful skill action with workflow/session updates",
                input_schema={"type": "object", "required": ["skill_id", "action"]},
                risk_class="read",
                required_extensions=[],
                approval_required=False,
                examples=["execute stateful skill"],
                idempotency="non_idempotent",
                side_effect_scope="file",
            ),
            cap(
                name="skill.execute.mutate",
                description="Execute mutate-tier skill action",
                input_schema={"type": "object", "required": ["skill_id", "action"]},
                risk_class="mutate",
                required_extensions=[],
                approval_required=True,
                examples=["execute mutate skill"],
                idempotency="non_idempotent",
                side_effect_scope="file",
            ),
            cap(
                name="skill.execute.destructive",
                description="Execute destructive-tier skill action",
                input_schema={"type": "object", "required": ["skill_id", "action"]},
                risk_class="destructive",
                required_extensions=[],
                approval_required=True,
                examples=["execute destructive skill"],
                idempotency="non_idempotent",
                side_effect_scope="file",
            ),
            # Legacy compatibility intents for staged migration.
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
                examples=["save spec"],
                idempotency="non_idempotent",
                side_effect_scope="none",
            ),
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
                examples=["save plan"],
                idempotency="non_idempotent",
                side_effect_scope="none",
            ),
        ]

    def _skills_dir(self) -> Path:
        return self.ctx.library_root / ".braindrive" / "skills"

    def _skills_fingerprint(self) -> Tuple[Tuple[str, int, int], ...]:
        root = self._skills_dir()
        if not root.exists():
            return tuple()

        items: List[Tuple[str, int, int]] = []
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            stat = path.stat()
            rel = str(path.relative_to(root)).replace("\\", "/")
            items.append((rel, int(stat.st_mtime_ns), int(stat.st_size)))
        return tuple(items)

    def _parse_manifest(self, manifest_path: Path) -> Dict[str, Any]:
        try:
            raw = manifest_path.read_text(encoding="utf-8")
        except OSError:
            return {}

        skill_match = re.search(r"^\s*skill_id\s*:\s*([A-Za-z0-9_-]+)\s*$", raw, flags=re.MULTILINE)
        if not skill_match:
            return {}

        actions: Dict[str, Dict[str, Any]] = {}
        lines = raw.splitlines()
        in_actions = False
        current_action = ""

        for line in lines:
            if not in_actions:
                if re.match(r"^\s*actions\s*:\s*$", line):
                    in_actions = True
                continue

            if re.match(r"^[A-Za-z0-9_-]", line):
                break

            action_match = re.match(r"^\s{2}([A-Za-z0-9_-]+)\s*:\s*$", line)
            if action_match:
                current_action = action_match.group(1)
                actions[current_action] = {
                    "execution_tier": "read",
                    "prompt_template": "",
                }
                continue

            if not current_action:
                continue

            tier_match = re.match(r"^\s{4}execution_tier\s*:\s*([A-Za-z0-9_-]+)\s*$", line)
            if tier_match:
                actions[current_action]["execution_tier"] = tier_match.group(1).strip()
                continue

            prompt_match = re.match(r"^\s{4}prompt_template\s*:\s*(.+?)\s*$", line)
            if prompt_match:
                actions[current_action]["prompt_template"] = prompt_match.group(1).strip().strip('"').strip("'")
                continue

        if not actions:
            actions = {
                "run": {
                    "execution_tier": "read",
                    "prompt_template": "prompts/run.md",
                }
            }

        return {
            "skill_id": skill_match.group(1).strip(),
            "manifest_dir": manifest_path.parent,
            "source": "manifest",
            "actions": actions,
        }

    def _legacy_actions_for(self, filename: str) -> Dict[str, Dict[str, Any]]:
        if filename == "interview.md":
            return {
                "start": {"execution_tier": "stateful", "prompt_file": filename},
                "continue": {"execution_tier": "stateful", "prompt_file": filename},
                "complete": {"execution_tier": "stateful", "prompt_file": filename},
            }
        if filename == "spec-generation.md":
            return {
                "generate": {"execution_tier": "read", "prompt_file": filename},
                "propose_save": {"execution_tier": "stateful", "prompt_file": filename},
            }
        if filename == "plan-generation.md":
            return {
                "generate": {"execution_tier": "read", "prompt_file": filename},
                "propose_save": {"execution_tier": "stateful", "prompt_file": filename},
            }
        return {
            "run": {"execution_tier": "read", "prompt_file": filename},
        }

    def _load_catalog(self) -> Dict[str, Dict[str, Any]]:
        root = self._skills_dir()
        root.mkdir(parents=True, exist_ok=True)

        fingerprint = self._skills_fingerprint()
        if fingerprint == self._catalog_fingerprint:
            return self._catalog_cache

        catalog: Dict[str, Dict[str, Any]] = {}

        for manifest in sorted(root.glob("*/skill.yaml")):
            parsed = self._parse_manifest(manifest)
            if not parsed:
                continue
            skill_id = str(parsed.get("skill_id", "")).strip()
            if not skill_id:
                continue
            catalog[skill_id] = parsed

        for legacy in sorted(root.glob("*.md")):
            filename = legacy.name
            skill_id = legacy.stem
            item = catalog.setdefault(
                skill_id,
                {
                    "skill_id": skill_id,
                    "source": "legacy_markdown",
                    "manifest_dir": root,
                    "actions": {},
                },
            )
            actions = item.setdefault("actions", {})
            if not isinstance(actions, dict):
                actions = {}
                item["actions"] = actions
            for action, meta in self._legacy_actions_for(filename).items():
                actions.setdefault(action, meta)

        self._catalog_cache = catalog
        self._catalog_fingerprint = fingerprint

        self.ctx.persistence.emit_event(
            "workflow",
            "skill.catalog.loaded",
            {
                "count": len(catalog),
                "skills": sorted(catalog.keys()),
            },
        )
        return catalog

    def _catalog_payload(self) -> Dict[str, Any]:
        catalog = self._load_catalog()
        out: List[Dict[str, Any]] = []
        for skill_id in sorted(catalog.keys()):
            entry = catalog[skill_id]
            actions = entry.get("actions", {}) if isinstance(entry.get("actions"), dict) else {}
            action_names = sorted(actions.keys())
            tiers = sorted({str(actions[name].get("execution_tier", "read")) for name in action_names if name in actions})
            out.append(
                {
                    "skill_id": skill_id,
                    "source": str(entry.get("source", "unknown")),
                    "actions": action_names,
                    "execution_tiers": tiers,
                }
            )
        return {
            "skills": out,
            "count": len(out),
            "skills_dir": str(self._skills_dir()),
        }

    def _resolve_action(self, skill_id: str, action: str, parent_message_id: str | None) -> Tuple[Dict[str, Any] | None, Dict[str, Any] | None]:
        catalog = self._load_catalog()
        skill = catalog.get(skill_id)
        if not isinstance(skill, dict):
            return None, make_error("E_NODE_ERROR", f"Unknown skill_id: {skill_id}", parent_message_id)

        actions = skill.get("actions", {})
        if not isinstance(actions, dict):
            return None, make_error("E_NODE_ERROR", f"Skill has no actions: {skill_id}", parent_message_id)

        action_meta = actions.get(action)
        if not isinstance(action_meta, dict):
            return None, make_error(
                "E_NODE_ERROR",
                f"Unknown action '{action}' for skill '{skill_id}'",
                parent_message_id,
            )

        execution_tier = str(action_meta.get("execution_tier", "read")).strip() or "read"
        resolved = {
            "skill_id": skill_id,
            "action": action,
            "execution_tier": execution_tier,
            "skill": skill,
            "action_meta": action_meta,
        }
        return resolved, None

    def _load_prompt(self, resolved: Dict[str, Any], parent_message_id: str | None) -> Tuple[str | None, Dict[str, Any] | None]:
        skill = resolved["skill"]
        action_meta = resolved["action_meta"]
        manifest_dir = skill.get("manifest_dir")
        if not isinstance(manifest_dir, Path):
            manifest_dir = self._skills_dir()

        prompt_path: Path
        prompt_template = str(action_meta.get("prompt_template", "")).strip()
        if prompt_template:
            prompt_path = (manifest_dir / prompt_template).resolve()
        else:
            prompt_file = str(action_meta.get("prompt_file", "")).strip()
            if not prompt_file:
                prompt_file = f"{resolved['skill_id']}.md"
            prompt_path = (self._skills_dir() / prompt_file).resolve()

        skills_root = self._skills_dir().resolve()
        try:
            prompt_path.relative_to(skills_root)
        except ValueError:
            return None, make_error(
                "E_NODE_ERROR",
                "Skill prompt path escapes skills directory",
                parent_message_id,
                details={"path": str(prompt_path)},
            )

        if not prompt_path.exists() or not prompt_path.is_file():
            return None, make_error(
                "E_NODE_ERROR",
                f"Skill prompt not found: {prompt_path.name}",
                parent_message_id,
                details={"path": str(prompt_path)},
            )

        try:
            return prompt_path.read_text(encoding="utf-8"), None
        except OSError as exc:
            return None, make_error(
                "E_NODE_ERROR",
                "Failed to read skill prompt",
                parent_message_id,
                details={"path": str(prompt_path), "error": str(exc)},
            )

    @staticmethod
    def _llm_ext(message: Dict[str, Any]) -> Dict[str, Any]:
        extensions = message.get("extensions", {})
        if not isinstance(extensions, dict):
            return {}
        llm = extensions.get("llm", {})
        return llm if isinstance(llm, dict) else {}

    def _model_info(self, llm_ext: Dict[str, Any]) -> Dict[str, str]:
        selection = self._config.select_llm(llm_ext if isinstance(llm_ext, dict) else None)
        return {
            "provider": selection.provider,
            "model": selection.model,
        }

    def _emit_exec_event(
        self,
        *,
        event_type: str,
        skill_id: str,
        action: str,
        execution_tier: str,
        folder: str,
        latency_ms: float | None,
        llm_ext: Dict[str, Any],
        error_code: str = "",
    ) -> None:
        model_info = self._model_info(llm_ext)
        payload: Dict[str, Any] = {
            "skill_id": skill_id,
            "action": action,
            "execution_tier": execution_tier,
            "folder": folder,
            "latency_ms": None if latency_ms is None else round(float(latency_ms), 2),
            "model": model_info,
        }
        if error_code:
            payload["error"] = {"code": error_code}
        self.ctx.persistence.emit_event("workflow", event_type, payload)

    def _active_folder(self) -> str:
        if self.ctx.workflow_state is None:
            return ""
        return str(self.ctx.workflow_state.read("active_folder", ""))

    def _folder_from_payload_context(self, payload: Dict[str, Any]) -> str:
        context = payload.get("context", {})
        if not isinstance(context, dict):
            return ""
        folder = context.get("folder", "")
        if not isinstance(folder, str):
            return ""
        return folder.strip()

    def _load_interview(self, folder: str) -> Dict[str, Any]:
        if self.ctx.workflow_state is None:
            return {"status": "idle", "answers": [], "question_index": 0, "asked_questions": []}

        state = self.ctx.workflow_state.get()
        interviews = state.get("interviews", {})
        if not isinstance(interviews, dict):
            return {"status": "idle", "answers": [], "question_index": 0, "asked_questions": []}

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

    def _save_skill_session(self, *, skill_id: str, folder: str, session: Dict[str, Any]) -> None:
        if self.ctx.workflow_state is None:
            return

        def _mutate(state: Dict[str, Any]) -> None:
            sessions = state.setdefault("skill_sessions", {})
            if not isinstance(sessions, dict):
                state["skill_sessions"] = {}
                sessions = state["skill_sessions"]
            by_skill = sessions.setdefault(skill_id, {})
            if not isinstance(by_skill, dict):
                sessions[skill_id] = {}
                by_skill = sessions[skill_id]
            by_skill[folder] = session

        self.ctx.workflow_state.mutate(_mutate)

    def _save_skill_output(self, *, skill_id: str, folder: str, output: Dict[str, Any]) -> None:
        if self.ctx.workflow_state is None:
            return

        def _mutate(state: Dict[str, Any]) -> None:
            outputs = state.setdefault("skill_outputs", {})
            if not isinstance(outputs, dict):
                state["skill_outputs"] = {}
                outputs = state["skill_outputs"]
            by_skill = outputs.setdefault(skill_id, {})
            if not isinstance(by_skill, dict):
                outputs[skill_id] = {}
                by_skill = outputs[skill_id]
            by_skill[folder] = output

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
        if len(text) <= MAX_INTERVIEW_HISTORY_CHARS:
            return text
        return (
            f"(Truncated to most recent {MAX_INTERVIEW_HISTORY_CHARS} characters)\n\n"
            + text[-MAX_INTERVIEW_HISTORY_CHARS:]
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

    def _interview_start(
        self,
        *,
        message: Dict[str, Any],
        folder: str,
        prompt_text: str,
        llm_ext: Dict[str, Any],
    ) -> Tuple[str, Dict[str, Any]] | Dict[str, Any]:
        interview = {
            "status": "in_progress",
            "answers": [],
            "question_index": 0,
            "asked_questions": [],
            "session_id": str(new_uuid()),
            "started_at": now_iso(),
        }

        driver = LLMSkillDriver(self.ctx)
        prompt = self._build_next_question_prompt(folder=folder, skill=prompt_text, answers=[])
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
        self._save_skill_session(skill_id="interview", folder=folder, session=interview)
        self._save_skill_output(skill_id="interview", folder=folder, output={"question": question, "question_index": 0})

        return (
            "workflow.interview.question",
            {
                "folder": folder,
                "question_index": 0,
                "question": question,
            },
        )

    def _interview_continue(
        self,
        *,
        message: Dict[str, Any],
        payload: Dict[str, Any],
        folder: str,
        prompt_text: str,
        llm_ext: Dict[str, Any],
    ) -> Tuple[str, Dict[str, Any]] | Dict[str, Any]:
        interview = self._load_interview(folder)
        if interview.get("status") != "in_progress":
            return make_error("E_NODE_ERROR", "Interview not started", message.get("message_id"))

        answer = str(payload.get("answer", "")).strip()
        if not answer:
            answer = str(payload.get("inputs", {}).get("answer", "")).strip() if isinstance(payload.get("inputs"), dict) else ""
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
            self._save_skill_session(skill_id="interview", folder=folder, session=interview)
            self._save_skill_output(
                skill_id="interview",
                folder=folder,
                output={
                    "answers_collected": len(answers),
                    "next": "workflow.interview.complete",
                },
            )
            return (
                "workflow.interview.ready",
                {
                    "folder": folder,
                    "answers_collected": len(answers),
                    "next": "workflow.interview.complete",
                },
            )

        driver = LLMSkillDriver(self.ctx)
        prompt = self._build_next_question_prompt(folder=folder, skill=prompt_text, answers=answers)
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
        self._save_skill_session(skill_id="interview", folder=folder, session=interview)
        self._save_skill_output(
            skill_id="interview",
            folder=folder,
            output={
                "question": question,
                "answers_collected": len(answers),
                "question_index": int(interview.get("question_index", 0)),
            },
        )
        return (
            "workflow.interview.question",
            {
                "folder": folder,
                "question_index": int(interview.get("question_index", 0)),
                "question": question,
                "answers_collected": len(answers),
            },
        )

    def _interview_complete(
        self,
        *,
        message: Dict[str, Any],
        folder: str,
        prompt_text: str,
        llm_ext: Dict[str, Any],
    ) -> Tuple[str, Dict[str, Any]] | Dict[str, Any]:
        interview = self._load_interview(folder)
        if interview.get("status") not in {"in_progress", "ready_to_complete"}:
            return make_error("E_NODE_ERROR", "Interview not started", message.get("message_id"))

        answers = interview.get("answers", [])
        if not isinstance(answers, list) or not answers:
            return make_error("E_NODE_ERROR", "Interview has no answers", message.get("message_id"))

        driver = LLMSkillDriver(self.ctx)
        prompt = self._build_completion_prompt(
            folder=folder,
            skill=prompt_text,
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
        self._save_skill_session(skill_id="interview", folder=folder, session=interview)
        self._save_skill_output(
            skill_id="interview",
            folder=folder,
            output={
                "summary": summary or "",
                "history_path": f"{folder}/interview.md",
                "answers_collected": len([item for item in answers if isinstance(item, dict)]),
                "session_id": str(interview.get("session_id", "")),
                "completed_at": str(interview.get("completed_at", "")),
            },
        )

        return (
            "workflow.interview.completed",
            {
                "folder": folder,
                "answers_collected": len([item for item in answers if isinstance(item, dict)]),
                "summary": summary or "",
                "history_path": f"{folder}/interview.md",
                "session_id": str(interview.get("session_id", "")),
                "completed_at": str(interview.get("completed_at", "")),
            },
        )

    def _spec_generate(
        self,
        *,
        message: Dict[str, Any],
        folder: str,
        prompt_text: str,
        llm_ext: Dict[str, Any],
    ) -> Tuple[str, Dict[str, Any]] | Dict[str, Any]:
        answers = self._interview_answers(folder)
        prompt = self._build_spec_prompt(
            folder=folder,
            skill=prompt_text,
            answers=answers,
            summary=self._interview_summary(folder),
        )
        generated, err = LLMSkillDriver(self.ctx).complete(
            prompt=prompt,
            parent_message_id=message.get("message_id"),
            llm_ext=llm_ext,
        )
        if err:
            return err
        assert generated is not None

        self._save_generated_spec(folder, generated)
        self._save_skill_output(skill_id="spec-generation", folder=folder, output={"spec_markdown": generated})
        return (
            "workflow.spec.generated",
            {
                "folder": folder,
                "spec_markdown": generated,
            },
        )

    def _spec_propose_save(
        self,
        *,
        message: Dict[str, Any],
        payload: Dict[str, Any],
        folder: str,
        prompt_text: str,
        llm_ext: Dict[str, Any],
    ) -> Tuple[str, Dict[str, Any]] | Dict[str, Any]:
        spec_markdown = str(payload.get("spec_markdown", "")).strip()
        if not spec_markdown:
            generated = self._spec_generate(
                message=message,
                folder=folder,
                prompt_text=prompt_text,
                llm_ext=llm_ext,
            )
            if isinstance(generated, dict) and generated.get("intent") == "error":
                return generated
            generated_intent, generated_payload = generated
            if generated_intent != "workflow.spec.generated":
                return make_error("E_NODE_ERROR", "Spec generation returned unexpected result", message.get("message_id"))
            spec_markdown = str(generated_payload.get("spec_markdown", "")).strip()

        self._save_generated_spec(folder, spec_markdown)
        self._save_skill_output(skill_id="spec-generation", folder=folder, output={"spec_markdown": spec_markdown})

        request_id = f"appr-{new_uuid()}"
        change = {
            "path": f"{folder}/spec.md",
            "operation": "write",
            "summary": "Save generated spec",
            "diff_preview": spec_markdown[:500],
        }
        return (
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
        )

    def _plan_generate(
        self,
        *,
        message: Dict[str, Any],
        folder: str,
        prompt_text: str,
        llm_ext: Dict[str, Any],
    ) -> Tuple[str, Dict[str, Any]] | Dict[str, Any]:
        spec_text = self._spec_text(folder)
        prompt = self._build_plan_prompt(folder=folder, skill=prompt_text, spec_text=spec_text)
        generated, err = LLMSkillDriver(self.ctx).complete(
            prompt=prompt,
            parent_message_id=message.get("message_id"),
            llm_ext=llm_ext,
        )
        if err:
            return err
        assert generated is not None

        self._save_skill_output(skill_id="plan-generation", folder=folder, output={"plan_markdown": generated})
        return (
            "workflow.plan.generated",
            {
                "folder": folder,
                "grounded_in_spec": bool(spec_text.strip()),
                "plan_markdown": generated,
            },
        )

    def _plan_propose_save(
        self,
        *,
        message: Dict[str, Any],
        payload: Dict[str, Any],
        folder: str,
        prompt_text: str,
        llm_ext: Dict[str, Any],
    ) -> Tuple[str, Dict[str, Any]] | Dict[str, Any]:
        plan_markdown = str(payload.get("plan_markdown", "")).strip()
        if not plan_markdown:
            generated = self._plan_generate(
                message=message,
                folder=folder,
                prompt_text=prompt_text,
                llm_ext=llm_ext,
            )
            if isinstance(generated, dict) and generated.get("intent") == "error":
                return generated
            generated_intent, generated_payload = generated
            if generated_intent != "workflow.plan.generated":
                return make_error("E_NODE_ERROR", "Plan generation returned unexpected result", message.get("message_id"))
            plan_markdown = str(generated_payload.get("plan_markdown", "")).strip()

        self._save_skill_output(skill_id="plan-generation", folder=folder, output={"plan_markdown": plan_markdown})
        request_id = f"appr-{new_uuid()}"
        change = {
            "path": f"{folder}/plan.md",
            "operation": "write",
            "summary": "Save generated plan",
            "diff_preview": plan_markdown[:500],
        }
        return (
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
        )

    def _generic_execute(
        self,
        *,
        message: Dict[str, Any],
        payload: Dict[str, Any],
        skill_id: str,
        action: str,
        folder: str,
        prompt_text: str,
        llm_ext: Dict[str, Any],
    ) -> Tuple[str, Dict[str, Any]] | Dict[str, Any]:
        inputs = payload.get("inputs", {})
        if not isinstance(inputs, dict):
            inputs = {}
        context = payload.get("context", {})
        if not isinstance(context, dict):
            context = {}

        driver = LLMSkillDriver(self.ctx)
        assembled_prompt = (
            f"{prompt_text}\n\n"
            f"Execute skill '{skill_id}' action '{action}'.\n"
            "Return concise markdown or plain text output matching the action intent.\n\n"
            f"Active folder: {folder or '(none)'}\n"
            f"Inputs JSON:\n{json.dumps(inputs, ensure_ascii=True, indent=2)}\n\n"
            f"Context JSON:\n{json.dumps(context, ensure_ascii=True, indent=2)}\n"
        )
        text, err = driver.complete(
            prompt=assembled_prompt,
            parent_message_id=message.get("message_id"),
            llm_ext=llm_ext,
        )
        if err:
            return err
        assert text is not None

        if folder:
            self._save_skill_output(
                skill_id=skill_id,
                folder=folder,
                output={
                    "action": action,
                    "text": text,
                },
            )

        return (
            "skill.executed",
            {
                "skill_id": skill_id,
                "action": action,
                "status": "ok",
                "result": {"text": text},
                "artifacts": [],
                "next_action": "complete",
            },
        )

    def _dispatch_action(
        self,
        *,
        message: Dict[str, Any],
        payload: Dict[str, Any],
        skill_id: str,
        action: str,
        execution_tier: str,
        folder: str,
        llm_ext: Dict[str, Any],
    ) -> Tuple[str, Dict[str, Any]] | Dict[str, Any]:
        resolved, err = self._resolve_action(skill_id, action, message.get("message_id"))
        if err:
            return err
        assert resolved is not None

        resolved_tier = str(resolved.get("execution_tier", "read")).strip() or "read"
        if resolved_tier != execution_tier:
            return make_error(
                "E_BAD_MESSAGE",
                f"Action tier mismatch: action '{action}' is '{resolved_tier}', requested '{execution_tier}'",
                message.get("message_id"),
            )

        prompt_text, prompt_err = self._load_prompt(resolved, message.get("message_id"))
        if prompt_err:
            return prompt_err
        assert prompt_text is not None

        if skill_id == "interview":
            if not folder:
                return make_error("E_NODE_ERROR", "No active folder selected", message.get("message_id"))
            if action == "start":
                return self._interview_start(
                    message=message,
                    folder=folder,
                    prompt_text=prompt_text,
                    llm_ext=llm_ext,
                )
            if action == "continue":
                return self._interview_continue(
                    message=message,
                    payload=payload,
                    folder=folder,
                    prompt_text=prompt_text,
                    llm_ext=llm_ext,
                )
            if action == "complete":
                return self._interview_complete(
                    message=message,
                    folder=folder,
                    prompt_text=prompt_text,
                    llm_ext=llm_ext,
                )

        if skill_id == "spec-generation":
            if not folder:
                return make_error("E_NODE_ERROR", "No active folder selected", message.get("message_id"))
            if action == "generate":
                return self._spec_generate(
                    message=message,
                    folder=folder,
                    prompt_text=prompt_text,
                    llm_ext=llm_ext,
                )
            if action == "propose_save":
                return self._spec_propose_save(
                    message=message,
                    payload=payload,
                    folder=folder,
                    prompt_text=prompt_text,
                    llm_ext=llm_ext,
                )

        if skill_id == "plan-generation":
            if not folder:
                return make_error("E_NODE_ERROR", "No active folder selected", message.get("message_id"))
            if action == "generate":
                return self._plan_generate(
                    message=message,
                    folder=folder,
                    prompt_text=prompt_text,
                    llm_ext=llm_ext,
                )
            if action == "propose_save":
                return self._plan_propose_save(
                    message=message,
                    payload=payload,
                    folder=folder,
                    prompt_text=prompt_text,
                    llm_ext=llm_ext,
                )

        return self._generic_execute(
            message=message,
            payload=payload,
            skill_id=skill_id,
            action=action,
            folder=folder,
            prompt_text=prompt_text,
            llm_ext=llm_ext,
        )

    def _execute_with_events(
        self,
        *,
        message: Dict[str, Any],
        payload: Dict[str, Any],
        skill_id: str,
        action: str,
        execution_tier: str,
        folder: str,
        llm_ext: Dict[str, Any],
    ) -> Tuple[str, Dict[str, Any]] | Dict[str, Any]:
        started = time.perf_counter()
        self._emit_exec_event(
            event_type="skill.execution.started",
            skill_id=skill_id,
            action=action,
            execution_tier=execution_tier,
            folder=folder,
            latency_ms=None,
            llm_ext=llm_ext,
        )

        result = self._dispatch_action(
            message=message,
            payload=payload,
            skill_id=skill_id,
            action=action,
            execution_tier=execution_tier,
            folder=folder,
            llm_ext=llm_ext,
        )

        latency_ms = (time.perf_counter() - started) * 1000.0
        if isinstance(result, dict) and result.get("intent") == "error":
            code = str(result.get("payload", {}).get("error", {}).get("code", ""))
            self._emit_exec_event(
                event_type="skill.execution.failed",
                skill_id=skill_id,
                action=action,
                execution_tier=execution_tier,
                folder=folder,
                latency_ms=latency_ms,
                llm_ext=llm_ext,
                error_code=code,
            )
            return result

        self._emit_exec_event(
            event_type="skill.execution.completed",
            skill_id=skill_id,
            action=action,
            execution_tier=execution_tier,
            folder=folder,
            latency_ms=latency_ms,
            llm_ext=llm_ext,
        )
        return result

    def handle(self, message: Dict[str, Any]) -> Dict[str, Any]:
        intent = str(message.get("intent", ""))
        payload = message.get("payload", {})
        if not isinstance(payload, dict):
            return make_error("E_BAD_MESSAGE", "payload must be object", message.get("message_id"))

        if intent == "skill.catalog.list":
            return make_response("skill.catalog", self._catalog_payload(), message.get("message_id"))

        if intent.startswith("skill.execute."):
            tier = intent.split(".")[-1].strip()
            skill_id = str(payload.get("skill_id", "")).strip()
            action = str(payload.get("action", "")).strip() or "run"
            if not skill_id:
                return make_error("E_BAD_MESSAGE", "skill_id is required", message.get("message_id"))

            folder = self._folder_from_payload_context(payload) or self._active_folder()
            llm_ext = self._llm_ext(message)

            executed = self._execute_with_events(
                message=message,
                payload=payload,
                skill_id=skill_id,
                action=action,
                execution_tier=tier,
                folder=folder,
                llm_ext=llm_ext,
            )
            if isinstance(executed, dict):
                return executed

            response_intent, response_payload = executed
            if response_intent == "approval.request":
                return make_response("approval.request", response_payload, message.get("message_id"))

            if response_intent == "skill.executed":
                return make_response("skill.executed", response_payload, message.get("message_id"))

            return make_response(
                "skill.executed",
                {
                    "skill_id": skill_id,
                    "action": action,
                    "status": "ok",
                    "result": {
                        "intent": response_intent,
                        "payload": response_payload,
                    },
                    "artifacts": [],
                    "next_action": "complete",
                },
                message.get("message_id"),
            )

        if intent in LEGACY_INTENT_MAP:
            mapping = LEGACY_INTENT_MAP[intent]
            skill_id = mapping["skill_id"]
            action = mapping["action"]
            tier = mapping["execution_tier"]
            folder = self._active_folder()
            llm_ext = self._llm_ext(message)

            executed = self._execute_with_events(
                message=message,
                payload=payload,
                skill_id=skill_id,
                action=action,
                execution_tier=tier,
                folder=folder,
                llm_ext=llm_ext,
            )
            if isinstance(executed, dict):
                return executed

            response_intent, response_payload = executed
            if response_intent == "skill.executed":
                # Legacy path should not leak generic intent shape.
                result = response_payload.get("result", {}) if isinstance(response_payload, dict) else {}
                if isinstance(result, dict):
                    response_payload = result
                response_intent = intent

            return make_response(response_intent, response_payload, message.get("message_id"))

        return make_error("E_NO_ROUTE", "Unsupported intent", message.get("message_id"))
