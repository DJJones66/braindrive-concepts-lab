from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

from ..protocol import make_error, make_response
from .base import ProtocolNode, cap


class SessionStateNode(ProtocolNode):
    node_id = "node.session.state"
    priority = 185

    def capabilities(self) -> List:
        return [
            cap(
                name="session.active_folder.get",
                description="Read current active folder",
                input_schema={"type": "object"},
                risk_class="read",
                required_extensions=[],
                approval_required=False,
                examples=["get active folder"],
                idempotency="idempotent",
                side_effect_scope="none",
            ),
            cap(
                name="session.active_folder.set",
                description="Set current active folder",
                input_schema={"type": "object", "required": ["active_folder"]},
                risk_class="read",
                required_extensions=[],
                approval_required=False,
                examples=["set active folder finances"],
                idempotency="idempotent",
                side_effect_scope="file",
            ),
            cap(
                name="session.interview.get",
                description="Read interview state for folder",
                input_schema={"type": "object", "required": ["folder"]},
                risk_class="read",
                required_extensions=[],
                approval_required=False,
                examples=["get interview state for folder"],
                idempotency="idempotent",
                side_effect_scope="none",
            ),
            cap(
                name="session.interview.put",
                description="Store interview state for folder",
                input_schema={"type": "object", "required": ["folder", "interview"]},
                risk_class="read",
                required_extensions=[],
                approval_required=False,
                examples=["save interview state"],
                idempotency="idempotent",
                side_effect_scope="file",
            ),
            cap(
                name="session.skill_session.put",
                description="Store skill session data for folder",
                input_schema={"type": "object", "required": ["skill_id", "folder", "session"]},
                risk_class="read",
                required_extensions=[],
                approval_required=False,
                examples=["save skill session"],
                idempotency="idempotent",
                side_effect_scope="file",
            ),
            cap(
                name="session.skill_output.get",
                description="Read latest skill output for folder",
                input_schema={"type": "object", "required": ["skill_id", "folder"]},
                risk_class="read",
                required_extensions=[],
                approval_required=False,
                examples=["get skill output"],
                idempotency="idempotent",
                side_effect_scope="none",
            ),
            cap(
                name="session.skill_output.put",
                description="Store latest skill output for folder",
                input_schema={"type": "object", "required": ["skill_id", "folder", "output"]},
                risk_class="read",
                required_extensions=[],
                approval_required=False,
                examples=["save skill output"],
                idempotency="idempotent",
                side_effect_scope="file",
            ),
            cap(
                name="session.interview_history.append",
                description="Append interview history entry for folder",
                input_schema={"type": "object", "required": ["folder", "session"]},
                risk_class="read",
                required_extensions=[],
                approval_required=False,
                examples=["append interview history"],
                idempotency="non_idempotent",
                side_effect_scope="file",
            ),
            cap(
                name="session.generated_spec.get",
                description="Read generated spec draft from session state",
                input_schema={"type": "object", "required": ["folder"]},
                risk_class="read",
                required_extensions=[],
                approval_required=False,
                examples=["get generated spec draft"],
                idempotency="idempotent",
                side_effect_scope="none",
            ),
            cap(
                name="session.generated_spec.put",
                description="Store generated spec draft in session state",
                input_schema={"type": "object", "required": ["folder", "markdown"]},
                risk_class="read",
                required_extensions=[],
                approval_required=False,
                examples=["save generated spec draft"],
                idempotency="idempotent",
                side_effect_scope="file",
            ),
        ]

    def _read_state(self, key: str, default: Any) -> Any:
        if self.ctx.workflow_state is None:
            return deepcopy(default)
        return self.ctx.workflow_state.read(key, deepcopy(default))

    def _mutate_state(self, fn) -> None:
        if self.ctx.workflow_state is None:
            return
        self.ctx.workflow_state.mutate(fn)

    def _active_folder(self) -> str:
        value = self._read_state("active_folder", "")
        return str(value).strip() if isinstance(value, str) else ""

    def _set_active_folder(self, folder: str) -> None:
        self._mutate_state(lambda state: state.__setitem__("active_folder", folder))

    def _interview(self, folder: str) -> Dict[str, Any]:
        interviews = self._read_state("interviews", {})
        if not isinstance(interviews, dict):
            return {}
        item = interviews.get(folder)
        return deepcopy(item) if isinstance(item, dict) else {}

    def _put_interview(self, folder: str, interview: Dict[str, Any]) -> None:
        def _mutate(state: Dict[str, Any]) -> None:
            interviews = state.setdefault("interviews", {})
            if not isinstance(interviews, dict):
                state["interviews"] = {}
                interviews = state["interviews"]
            interviews[folder] = deepcopy(interview)

        self._mutate_state(_mutate)

    def _put_skill_session(self, skill_id: str, folder: str, session: Dict[str, Any]) -> None:
        def _mutate(state: Dict[str, Any]) -> None:
            sessions = state.setdefault("skill_sessions", {})
            if not isinstance(sessions, dict):
                state["skill_sessions"] = {}
                sessions = state["skill_sessions"]
            by_skill = sessions.setdefault(skill_id, {})
            if not isinstance(by_skill, dict):
                sessions[skill_id] = {}
                by_skill = sessions[skill_id]
            by_skill[folder] = deepcopy(session)

        self._mutate_state(_mutate)

    def _skill_output(self, skill_id: str, folder: str) -> Dict[str, Any]:
        outputs = self._read_state("skill_outputs", {})
        if not isinstance(outputs, dict):
            return {}
        by_skill = outputs.get(skill_id)
        if not isinstance(by_skill, dict):
            return {}
        item = by_skill.get(folder)
        return deepcopy(item) if isinstance(item, dict) else {}

    def _put_skill_output(self, skill_id: str, folder: str, output: Dict[str, Any]) -> None:
        def _mutate(state: Dict[str, Any]) -> None:
            outputs = state.setdefault("skill_outputs", {})
            if not isinstance(outputs, dict):
                state["skill_outputs"] = {}
                outputs = state["skill_outputs"]
            by_skill = outputs.setdefault(skill_id, {})
            if not isinstance(by_skill, dict):
                outputs[skill_id] = {}
                by_skill = outputs[skill_id]
            by_skill[folder] = deepcopy(output)

        self._mutate_state(_mutate)

    def _append_interview_history(self, folder: str, session: Dict[str, Any]) -> None:
        def _mutate(state: Dict[str, Any]) -> None:
            history = state.setdefault("interview_history", {})
            if not isinstance(history, dict):
                state["interview_history"] = {}
                history = state["interview_history"]
            entries = history.setdefault(folder, [])
            if not isinstance(entries, list):
                history[folder] = []
                entries = history[folder]
            entries.append(deepcopy(session))

        self._mutate_state(_mutate)

    def _generated_spec(self, folder: str) -> str:
        generated = self._read_state("generated_specs", {})
        if not isinstance(generated, dict):
            return ""
        value = generated.get(folder, "")
        return str(value) if value is not None else ""

    def _put_generated_spec(self, folder: str, markdown: str) -> None:
        def _mutate(state: Dict[str, Any]) -> None:
            generated = state.setdefault("generated_specs", {})
            if not isinstance(generated, dict):
                state["generated_specs"] = {}
                generated = state["generated_specs"]
            generated[folder] = markdown

        self._mutate_state(_mutate)

    def handle(self, message: Dict[str, Any]) -> Dict[str, Any]:
        intent = str(message.get("intent", ""))
        payload = message.get("payload", {})
        if not isinstance(payload, dict):
            return make_error("E_BAD_MESSAGE", "payload must be object", message.get("message_id"))

        if intent == "session.active_folder.get":
            return make_response(
                "session.active_folder",
                {"active_folder": self._active_folder()},
                message.get("message_id"),
            )

        if intent == "session.active_folder.set":
            folder = str(payload.get("active_folder", "")).strip()
            self._set_active_folder(folder)
            return make_response(
                "session.active_folder.updated",
                {"active_folder": folder},
                message.get("message_id"),
            )

        if intent == "session.interview.get":
            folder = str(payload.get("folder", "")).strip()
            if not folder:
                return make_error("E_BAD_MESSAGE", "folder is required", message.get("message_id"))
            return make_response(
                "session.interview",
                {"folder": folder, "interview": self._interview(folder)},
                message.get("message_id"),
            )

        if intent == "session.interview.put":
            folder = str(payload.get("folder", "")).strip()
            interview = payload.get("interview", {})
            if not folder:
                return make_error("E_BAD_MESSAGE", "folder is required", message.get("message_id"))
            if not isinstance(interview, dict):
                return make_error("E_BAD_MESSAGE", "interview must be object", message.get("message_id"))
            self._put_interview(folder, interview)
            return make_response(
                "session.interview.updated",
                {"folder": folder},
                message.get("message_id"),
            )

        if intent == "session.skill_session.put":
            skill_id = str(payload.get("skill_id", "")).strip()
            folder = str(payload.get("folder", "")).strip()
            session = payload.get("session", {})
            if not skill_id or not folder:
                return make_error("E_BAD_MESSAGE", "skill_id and folder are required", message.get("message_id"))
            if not isinstance(session, dict):
                return make_error("E_BAD_MESSAGE", "session must be object", message.get("message_id"))
            self._put_skill_session(skill_id, folder, session)
            return make_response(
                "session.skill_session.updated",
                {"skill_id": skill_id, "folder": folder},
                message.get("message_id"),
            )

        if intent == "session.skill_output.get":
            skill_id = str(payload.get("skill_id", "")).strip()
            folder = str(payload.get("folder", "")).strip()
            if not skill_id or not folder:
                return make_error("E_BAD_MESSAGE", "skill_id and folder are required", message.get("message_id"))
            return make_response(
                "session.skill_output",
                {"skill_id": skill_id, "folder": folder, "output": self._skill_output(skill_id, folder)},
                message.get("message_id"),
            )

        if intent == "session.skill_output.put":
            skill_id = str(payload.get("skill_id", "")).strip()
            folder = str(payload.get("folder", "")).strip()
            output = payload.get("output", {})
            if not skill_id or not folder:
                return make_error("E_BAD_MESSAGE", "skill_id and folder are required", message.get("message_id"))
            if not isinstance(output, dict):
                return make_error("E_BAD_MESSAGE", "output must be object", message.get("message_id"))
            self._put_skill_output(skill_id, folder, output)
            return make_response(
                "session.skill_output.updated",
                {"skill_id": skill_id, "folder": folder},
                message.get("message_id"),
            )

        if intent == "session.interview_history.append":
            folder = str(payload.get("folder", "")).strip()
            session = payload.get("session", {})
            if not folder:
                return make_error("E_BAD_MESSAGE", "folder is required", message.get("message_id"))
            if not isinstance(session, dict):
                return make_error("E_BAD_MESSAGE", "session must be object", message.get("message_id"))
            self._append_interview_history(folder, session)
            return make_response(
                "session.interview_history.appended",
                {"folder": folder},
                message.get("message_id"),
            )

        if intent == "session.generated_spec.get":
            folder = str(payload.get("folder", "")).strip()
            if not folder:
                return make_error("E_BAD_MESSAGE", "folder is required", message.get("message_id"))
            return make_response(
                "session.generated_spec",
                {"folder": folder, "markdown": self._generated_spec(folder)},
                message.get("message_id"),
            )

        if intent == "session.generated_spec.put":
            folder = str(payload.get("folder", "")).strip()
            markdown = payload.get("markdown", "")
            if not folder:
                return make_error("E_BAD_MESSAGE", "folder is required", message.get("message_id"))
            if not isinstance(markdown, str):
                return make_error("E_BAD_MESSAGE", "markdown must be string", message.get("message_id"))
            self._put_generated_spec(folder, markdown)
            return make_response(
                "session.generated_spec.updated",
                {"folder": folder},
                message.get("message_id"),
            )

        return make_error("E_NO_ROUTE", "Unsupported intent", message.get("message_id"))
