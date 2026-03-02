from __future__ import annotations

import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Tuple

from shared.bdp import append_jsonl, make_error, make_response, now_iso, validate_core
from shared.node_runtime import start_registration_loop

PORT = int(os.getenv("MARKDOWN_LIBRARY_PORT", "8114"))
SERVICE_NAME = os.getenv("MARKDOWN_LIBRARY_SERVICE_NAME", "node.markdown.library")
DATA_DIR = Path(os.getenv("MARKDOWN_LIBRARY_DATA_DIR", "/workspace/data/markdown-library"))
EVENT_LOG = DATA_DIR / "events.jsonl"


def _seed_files() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    seeds = {
        "welcome.md": "# Welcome\n\nThis is a fake markdown capability node for Concept-2 intent tests.\n",
        "q2-roadmap.md": "# Q2 Roadmap\n\n- Finalize router beta\n- Validate human-in-the-loop intent lab\n",
    }
    for filename, content in seeds.items():
        path = DATA_DIR / filename
        if not path.exists():
            path.write_text(content, encoding="utf-8")


def _log(event_type: str, payload: Dict[str, Any]) -> None:
    append_jsonl(
        EVENT_LOG,
        {
            "ts": now_iso(),
            "event_type": event_type,
            "payload": payload,
        },
    )


def _safe_slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9\-\s_]", "", value.strip().lower())
    slug = re.sub(r"[\s_]+", "-", slug).strip("-")
    return slug or "untitled-note"


def _safe_note_path(note_id: str) -> Path:
    candidate = DATA_DIR / f"{_safe_slug(note_id)}.md"
    resolved = candidate.resolve()
    if DATA_DIR.resolve() not in resolved.parents and resolved != DATA_DIR.resolve():
        raise ValueError("unsafe note path")
    return resolved


def _check_required_extensions(message: Dict[str, Any], required: List[str]) -> List[str]:
    extensions = message.get("extensions", {}) or {}
    return [item for item in required if item not in extensions]


def _read_all_notes() -> List[Tuple[str, Path]]:
    notes: List[Tuple[str, Path]] = []
    for file_path in sorted(DATA_DIR.glob("*.md")):
        notes.append((file_path.stem, file_path))
    return notes


def _intent_list_notes(message: Dict[str, Any]) -> Dict[str, Any]:
    notes = []
    for note_id, path in _read_all_notes():
        stats = path.stat()
        notes.append(
            {
                "note_id": note_id,
                "filename": path.name,
                "size_bytes": int(stats.st_size),
                "updated_at": now_iso(),
            }
        )
    _log("notes_listed", {"count": len(notes)})
    return make_response("md.library.notes_listed", {"notes": notes}, message.get("message_id"))


def _intent_read_note(message: Dict[str, Any]) -> Dict[str, Any]:
    payload = message.get("payload", {})
    note_id = str(payload.get("note_id", "")).strip()
    if not note_id:
        return make_error("E_BAD_MESSAGE", "payload.note_id is required", message.get("message_id"))

    path = _safe_note_path(note_id)
    if not path.exists():
        return make_error("E_NO_ROUTE", f"Note not found: {note_id}", message.get("message_id"))

    content = path.read_text(encoding="utf-8")
    _log("note_read", {"note_id": note_id, "bytes": len(content.encode("utf-8"))})
    return make_response(
        "md.library.note_read",
        {
            "note_id": note_id,
            "filename": path.name,
            "content": content,
        },
        message.get("message_id"),
    )


def _intent_create_note(message: Dict[str, Any]) -> Dict[str, Any]:
    payload = message.get("payload", {})
    title = str(payload.get("title", "")).strip() or "Untitled Note"
    content = str(payload.get("content", "")).strip()
    note_id = _safe_slug(title)
    path = _safe_note_path(note_id)

    if path.exists():
        return make_error("E_NODE_ERROR", f"Note already exists: {note_id}", message.get("message_id"))

    body = content or f"# {title}\n\nCreated by fake markdown library node.\n"
    path.write_text(body + ("\n" if not body.endswith("\n") else ""), encoding="utf-8")

    _log("note_created", {"note_id": note_id, "filename": path.name})
    return make_response(
        "md.library.note_created",
        {
            "note_id": note_id,
            "filename": path.name,
            "created": True,
        },
        message.get("message_id"),
    )


def _intent_append_note(message: Dict[str, Any]) -> Dict[str, Any]:
    payload = message.get("payload", {})
    note_id = str(payload.get("note_id", "")).strip()
    append_text = str(payload.get("append_text", "")).strip()
    if not note_id:
        return make_error("E_BAD_MESSAGE", "payload.note_id is required", message.get("message_id"))
    if not append_text:
        return make_error("E_BAD_MESSAGE", "payload.append_text is required", message.get("message_id"))

    path = _safe_note_path(note_id)
    if not path.exists():
        return make_error("E_NO_ROUTE", f"Note not found: {note_id}", message.get("message_id"))

    with path.open("a", encoding="utf-8") as handle:
        if path.stat().st_size > 0:
            handle.write("\n")
        handle.write(append_text)
        if not append_text.endswith("\n"):
            handle.write("\n")

    _log("note_appended", {"note_id": note_id, "added_chars": len(append_text)})
    return make_response(
        "md.library.note_appended",
        {
            "note_id": note_id,
            "appended": True,
        },
        message.get("message_id"),
    )


def _intent_search_notes(message: Dict[str, Any]) -> Dict[str, Any]:
    payload = message.get("payload", {})
    query = str(payload.get("query", "")).strip()
    if not query:
        return make_error("E_BAD_MESSAGE", "payload.query is required", message.get("message_id"))

    qlower = query.lower()
    matches = []
    for note_id, path in _read_all_notes():
        content = path.read_text(encoding="utf-8")
        if qlower in content.lower() or qlower in note_id.lower():
            preview = content[:140].replace("\n", " ").strip()
            matches.append({"note_id": note_id, "filename": path.name, "preview": preview})

    _log("notes_searched", {"query": query, "matches": len(matches)})
    return make_response(
        "md.library.notes_searched",
        {
            "query": query,
            "matches": matches,
        },
        message.get("message_id"),
    )


def _intent_delete_note(message: Dict[str, Any]) -> Dict[str, Any]:
    payload = message.get("payload", {})
    note_id = str(payload.get("note_id", "")).strip()
    if not note_id:
        return make_error("E_BAD_MESSAGE", "payload.note_id is required", message.get("message_id"))

    confirmation = (message.get("extensions", {}) or {}).get("confirmation", {})
    if not isinstance(confirmation, dict) or not bool(confirmation.get("confirmed", False)):
        return make_error("E_CONFIRMATION_REQUIRED", "confirmation.required for note deletion", message.get("message_id"))

    path = _safe_note_path(note_id)
    if not path.exists():
        return make_error("E_NO_ROUTE", f"Note not found: {note_id}", message.get("message_id"))

    path.unlink(missing_ok=False)
    _log("note_deleted", {"note_id": note_id})
    return make_response(
        "md.library.note_deleted",
        {
            "note_id": note_id,
            "deleted": True,
        },
        message.get("message_id"),
    )


class MarkdownLibraryHandler(BaseHTTPRequestHandler):
    server_version = "node.markdown.library/0.1"

    def _send_json(self, code: int, body: Dict[str, Any]) -> None:
        payload = json.dumps(body, ensure_ascii=True).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _read_json(self) -> Dict[str, Any] | None:
        try:
            size = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(size)
            parsed = json.loads(raw.decode("utf-8"))
        except Exception:
            return None
        return parsed if isinstance(parsed, dict) else None

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json(200, {"ok": True, "service": SERVICE_NAME, "notes": len(_read_all_notes())})
            return
        self._send_json(404, {"ok": False})

    def do_POST(self) -> None:
        if self.path != "/bdp":
            self._send_json(404, {"ok": False})
            return

        message = self._read_json()
        if message is None:
            self._send_json(200, make_error("E_BAD_MESSAGE", "Invalid JSON body", None))
            return

        validation_error = validate_core(message)
        if validation_error:
            self._send_json(200, validation_error)
            return

        intent = str(message.get("intent", ""))
        required_by_intent = {
            "md.library.list_notes": ["identity"],
            "md.library.read_note": ["identity"],
            "md.library.create_note": ["identity", "authz"],
            "md.library.append_note": ["identity", "authz"],
            "md.library.search_notes": ["identity"],
            "md.library.delete_note": ["identity", "authz", "confirmation"],
        }

        if intent not in required_by_intent:
            self._send_json(
                200,
                make_error(
                    "E_NO_ROUTE",
                    f"{SERVICE_NAME} cannot handle intent {intent}",
                    message.get("message_id"),
                ),
            )
            return

        missing = _check_required_extensions(message, required_by_intent[intent])
        if missing:
            self._send_json(
                200,
                make_error(
                    "E_REQUIRED_EXTENSION_MISSING",
                    "Missing required extension(s): " + ", ".join(missing),
                    message.get("message_id"),
                    details={"missing": missing},
                ),
            )
            return

        try:
            if intent == "md.library.list_notes":
                response = _intent_list_notes(message)
            elif intent == "md.library.read_note":
                response = _intent_read_note(message)
            elif intent == "md.library.create_note":
                response = _intent_create_note(message)
            elif intent == "md.library.append_note":
                response = _intent_append_note(message)
            elif intent == "md.library.search_notes":
                response = _intent_search_notes(message)
            else:
                response = _intent_delete_note(message)
        except Exception as exc:
            response = make_error(
                "E_INTERNAL",
                f"{SERVICE_NAME} exception: {type(exc).__name__}",
                message.get("message_id"),
                details={"error": str(exc)},
            )

        self._send_json(200, response)

    def log_message(self, format: str, *args: Any) -> None:
        return


def main() -> None:
    _seed_files()
    start_registration_loop()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), MarkdownLibraryHandler)
    print(f"{SERVICE_NAME} listening on :{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
