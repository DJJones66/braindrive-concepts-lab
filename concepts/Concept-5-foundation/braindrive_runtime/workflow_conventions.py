from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple


WORKFLOW_CONFIG_REL_PATH = Path(".braindrive/system/workflow-config.json")

DEFAULT_INTENT_ALIASES: Dict[str, Tuple[str, ...]] = {
    "workflow.interview.start": ("start interview", "interview me"),
    "workflow.interview.continue": ("continue interview", "my answer", "answer:"),
    "workflow.interview.complete": ("complete interview", "finish interview"),
    "workflow.spec.generate": ("generate spec", "draft spec"),
    "workflow.spec.propose_save": ("save spec", "propose spec"),
    "workflow.plan.generate": ("generate plan", "draft plan"),
    "workflow.plan.propose_save": ("save plan", "propose plan"),
}

DEFAULT_LEGACY_INTENT_MAP: Dict[str, Dict[str, str]] = {
    "workflow.interview.start": {"skill_id": "interview", "action": "start", "execution_tier": "stateful"},
    "workflow.interview.continue": {"skill_id": "interview", "action": "continue", "execution_tier": "stateful"},
    "workflow.interview.complete": {"skill_id": "interview", "action": "complete", "execution_tier": "stateful"},
    "workflow.spec.generate": {"skill_id": "spec-generation", "action": "generate", "execution_tier": "read"},
    "workflow.spec.propose_save": {"skill_id": "spec-generation", "action": "propose_save", "execution_tier": "stateful"},
    "workflow.plan.generate": {"skill_id": "plan-generation", "action": "generate", "execution_tier": "read"},
    "workflow.plan.propose_save": {"skill_id": "plan-generation", "action": "propose_save", "execution_tier": "stateful"},
}

DEFAULT_LEGACY_ACTION_BEHAVIOR: Dict[Tuple[str, str], Dict[str, str]] = {
    ("interview", "start"): {
        "operation": "session.start",
        "question_intent": "workflow.interview.question",
    },
    ("interview", "continue"): {
        "operation": "session.step",
        "question_intent": "workflow.interview.question",
        "ready_intent": "workflow.interview.ready",
        "next_intent": "workflow.interview.complete",
    },
    ("interview", "complete"): {
        "operation": "session.complete",
        "completed_intent": "workflow.interview.completed",
    },
    ("spec-generation", "generate"): {
        "operation": "artifact.generate",
        "artifact_kind": "spec",
        "generated_intent": "workflow.spec.generated",
    },
    ("spec-generation", "propose_save"): {
        "operation": "artifact.propose_save",
        "artifact_kind": "spec",
        "generated_intent": "workflow.spec.generated",
        "save_path": "spec.md",
        "save_summary": "Save generated spec",
    },
    ("plan-generation", "generate"): {
        "operation": "artifact.generate",
        "artifact_kind": "plan",
        "generated_intent": "workflow.plan.generated",
    },
    ("plan-generation", "propose_save"): {
        "operation": "artifact.propose_save",
        "artifact_kind": "plan",
        "generated_intent": "workflow.plan.generated",
        "save_path": "plan.md",
        "save_summary": "Save generated plan",
    },
}


@dataclass
class WorkflowConventions:
    notes_path: str
    spec_path: str
    plan_path: str
    agent_path: str
    interview_path: str
    context_docs: Tuple[str, ...]
    intent_aliases: Dict[str, Tuple[str, ...]]
    legacy_intent_map: Dict[str, Dict[str, str]]
    legacy_action_behavior: Dict[Tuple[str, str], Dict[str, str]]


def workflow_config_path(library_root: Path) -> Path:
    return library_root / WORKFLOW_CONFIG_REL_PATH


def default_workflow_config_payload() -> Dict[str, Any]:
    legacy_action_behavior: Dict[str, Dict[str, str]] = {}
    for (skill_id, action), behavior in DEFAULT_LEGACY_ACTION_BEHAVIOR.items():
        legacy_action_behavior[f"{skill_id}.{action}"] = dict(behavior)

    return {
        "paths": {
            "notes": "notes.md",
            "spec": "spec.md",
            "plan": "plan.md",
            "agent": "AGENT.md",
            "interview": "interview.md",
        },
        "context_docs": ["AGENT.md", "spec.md", "plan.md"],
        "intent_aliases": {key: list(values) for key, values in DEFAULT_INTENT_ALIASES.items()},
        "legacy_intent_map": {key: dict(value) for key, value in DEFAULT_LEGACY_INTENT_MAP.items()},
        "legacy_action_behavior": legacy_action_behavior,
    }


def _emit_warning(persistence: Any, event: str, details: Dict[str, Any]) -> None:
    if persistence is None:
        return
    emit = getattr(persistence, "emit_event", None)
    if not callable(emit):
        return
    try:
        emit("workflow", event, details)
    except Exception:
        return


def _clean_filename(value: Any, fallback: str) -> str:
    text = str(value).strip()
    if not text:
        return fallback
    normalized = text.replace("\\", "/").strip("/")
    return normalized or fallback


def _clean_string_list(raw: Any) -> List[str]:
    if not isinstance(raw, list):
        return []
    out: List[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        value = item.strip()
        if value:
            out.append(value)
    return out


def _parse_intent_aliases(raw: Any) -> Dict[str, Tuple[str, ...]]:
    aliases: Dict[str, Tuple[str, ...]] = {key: tuple(value) for key, value in DEFAULT_INTENT_ALIASES.items()}
    if not isinstance(raw, dict):
        return aliases
    for key, value in raw.items():
        intent = str(key).strip()
        if not intent:
            continue
        cleaned = tuple(item.lower() for item in _clean_string_list(value))
        if cleaned:
            aliases[intent] = cleaned
    return aliases


def _parse_legacy_intent_map(raw: Any) -> Dict[str, Dict[str, str]]:
    out: Dict[str, Dict[str, str]] = {key: dict(value) for key, value in DEFAULT_LEGACY_INTENT_MAP.items()}
    if not isinstance(raw, dict):
        return out
    for key, value in raw.items():
        intent = str(key).strip()
        if not intent or not isinstance(value, dict):
            continue
        skill_id = str(value.get("skill_id", "")).strip()
        action = str(value.get("action", "")).strip()
        execution_tier = str(value.get("execution_tier", "")).strip()
        if not skill_id or not action:
            continue
        item = {
            "skill_id": skill_id,
            "action": action,
            "execution_tier": execution_tier or "read",
        }
        out[intent] = item
    return out


def _parse_legacy_action_behavior(raw: Any) -> Dict[Tuple[str, str], Dict[str, str]]:
    out: Dict[Tuple[str, str], Dict[str, str]] = {key: dict(value) for key, value in DEFAULT_LEGACY_ACTION_BEHAVIOR.items()}
    if not isinstance(raw, dict):
        return out
    for key, value in raw.items():
        if not isinstance(value, dict):
            continue
        composite = str(key).strip()
        if "." not in composite:
            continue
        skill_id, action = composite.split(".", 1)
        skill_id = skill_id.strip()
        action = action.strip()
        if not skill_id or not action:
            continue
        behavior: Dict[str, str] = {}
        for field, field_value in value.items():
            name = str(field).strip()
            if not name:
                continue
            text = str(field_value).strip()
            if text:
                behavior[name] = text
        if behavior:
            out[(skill_id, action)] = behavior
    return out


def _to_conventions(payload: Mapping[str, Any]) -> WorkflowConventions:
    raw_paths = payload.get("paths", {})
    paths = raw_paths if isinstance(raw_paths, dict) else {}
    notes_path = _clean_filename(paths.get("notes"), "notes.md")
    spec_path = _clean_filename(paths.get("spec"), "spec.md")
    plan_path = _clean_filename(paths.get("plan"), "plan.md")
    agent_path = _clean_filename(paths.get("agent"), "AGENT.md")
    interview_path = _clean_filename(paths.get("interview"), "interview.md")

    raw_context_docs = _clean_string_list(payload.get("context_docs"))
    if raw_context_docs:
        context_docs = tuple(_clean_filename(item, item) for item in raw_context_docs)
    else:
        context_docs = (agent_path, spec_path, plan_path)

    legacy_action_behavior = _parse_legacy_action_behavior(payload.get("legacy_action_behavior"))
    spec_save = legacy_action_behavior.setdefault(("spec-generation", "propose_save"), {})
    spec_save["save_path"] = spec_path

    plan_save = legacy_action_behavior.setdefault(("plan-generation", "propose_save"), {})
    plan_save["save_path"] = plan_path

    return WorkflowConventions(
        notes_path=notes_path,
        spec_path=spec_path,
        plan_path=plan_path,
        agent_path=agent_path,
        interview_path=interview_path,
        context_docs=context_docs,
        intent_aliases=_parse_intent_aliases(payload.get("intent_aliases")),
        legacy_intent_map=_parse_legacy_intent_map(payload.get("legacy_intent_map")),
        legacy_action_behavior=legacy_action_behavior,
    )


def load_workflow_conventions(library_root: Path, persistence: Any = None) -> WorkflowConventions:
    defaults = default_workflow_config_payload()
    path = workflow_config_path(library_root)
    if not path.exists() or not path.is_file():
        return _to_conventions(defaults)

    try:
        raw_text = path.read_text(encoding="utf-8")
        parsed = json.loads(raw_text)
    except Exception as exc:
        _emit_warning(
            persistence,
            "workflow.config.invalid",
            {
                "path": str(path),
                "error": str(exc),
            },
        )
        return _to_conventions(defaults)

    if not isinstance(parsed, dict):
        _emit_warning(
            persistence,
            "workflow.config.invalid_shape",
            {
                "path": str(path),
            },
        )
        return _to_conventions(defaults)

    merged = default_workflow_config_payload()
    for key in ["paths", "intent_aliases", "legacy_intent_map", "legacy_action_behavior"]:
        raw_value = parsed.get(key)
        if isinstance(raw_value, dict):
            section = merged.get(key)
            if not isinstance(section, dict):
                section = {}
            section.update(raw_value)
            merged[key] = section
    if isinstance(parsed.get("context_docs"), list):
        merged["context_docs"] = parsed.get("context_docs", [])

    return _to_conventions(merged)
