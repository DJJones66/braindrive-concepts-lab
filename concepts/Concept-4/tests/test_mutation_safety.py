from __future__ import annotations

import subprocess


def _commit_count(runtime) -> int:
    result = subprocess.run(
        ["git", "-C", str(runtime.library_root), "rev-list", "--count", "HEAD"],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return 0
    return int(result.stdout.strip() or "0")


def _activate_folder(runtime, make_message):
    runtime.route(
        make_message(
            "folder.create",
            {"topic": "Finances"},
            {"confirmation": {"required": True, "status": "approved", "request_id": "appr-folder"}},
        )
    )
    runtime.route(make_message("folder.switch", {"folder": "finances"}))


def test_mutation_blocked_without_confirmation(runtime, make_message):
    response = runtime.route(make_message("memory.write.propose", {"path": "x.md", "content": "hello"}))
    assert response["intent"] == "error"
    assert response["payload"]["error"]["code"] == "E_CONFIRMATION_REQUIRED"


def test_denied_approval_has_no_side_effects(runtime, make_message):
    _activate_folder(runtime, make_message)

    proposal = runtime.route(make_message("workflow.spec.propose_save", {}))
    assert proposal["intent"] == "approval.request"

    before_count = _commit_count(runtime)

    outcome = runtime.apply_approval_flow(proposal["payload"], approve=False)
    assert outcome["approval_resolve"]["payload"]["decision"] == "denied"

    spec_path = runtime.library_root / "finances" / "spec.md"
    assert not spec_path.exists()
    assert _commit_count(runtime) == before_count


def test_approved_mutation_writes_and_commits_once(runtime, make_message):
    _activate_folder(runtime, make_message)

    proposal = runtime.route(make_message("workflow.spec.propose_save", {}))
    assert proposal["intent"] == "approval.request"

    before_count = _commit_count(runtime)
    outcome = runtime.apply_approval_flow(proposal["payload"], approve=True)

    assert outcome["approval_resolve"]["payload"]["decision"] == "approved"
    assert outcome["write"]["intent"] == "memory.write.applied"
    assert outcome["commit"]["intent"] in {"git.committed", "git.commit.skipped"}

    spec_path = runtime.library_root / "finances" / "spec.md"
    assert spec_path.exists()

    after_count = _commit_count(runtime)
    assert after_count == before_count + 1
