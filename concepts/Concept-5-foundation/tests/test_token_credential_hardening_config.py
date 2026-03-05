from __future__ import annotations

from pathlib import Path

import pytest

from braindrive_runtime.runtime import BrainDriveRuntime
from braindrive_runtime.security import LEGACY_DEFAULT_REGISTRATION_TOKEN, validate_registration_token


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def test_registration_token_validator_rejects_empty_and_legacy_default() -> None:
    with pytest.raises(ValueError):
        validate_registration_token("")
    with pytest.raises(ValueError):
        validate_registration_token(LEGACY_DEFAULT_REGISTRATION_TOKEN)


def test_runtime_default_registration_token_is_generated_and_non_legacy(tmp_path: Path) -> None:
    runtime = BrainDriveRuntime(
        library_root=tmp_path / "library",
        data_root=tmp_path / "runtime",
        env={},
    )
    assert runtime.registration_token
    assert runtime.registration_token != LEGACY_DEFAULT_REGISTRATION_TOKEN


def test_env_example_removes_legacy_static_registration_token_default() -> None:
    source = (_repo_root() / ".env.example").read_text(encoding="utf-8")
    assert "BRAINDRIVE_DEV_MODE=true" in source
    assert "ROUTER_REGISTRATION_TOKEN=" in source
    assert "ROUTER_REGISTRATION_TOKEN=braindrive-mvp-dev-token" not in source


def test_compose_uses_non_legacy_token_passthrough() -> None:
    source = (_repo_root() / "docker-compose.yml").read_text(encoding="utf-8")
    assert "braindrive-mvp-dev-token" not in source
    assert source.count('ROUTER_REGISTRATION_TOKEN: "${ROUTER_REGISTRATION_TOKEN:-}"') >= 2


def test_bootstrap_generates_and_injects_registration_token() -> None:
    source = (_repo_root() / "scripts" / "bootstrap.sh").read_text(encoding="utf-8")
    assert "generate_registration_token()" in source
    assert "resolve_registration_token()" in source
    assert 'upsert_env_value "ROUTER_REGISTRATION_TOKEN"' in source
    assert 'ROUTER_REGISTRATION_TOKEN_VALUE="$(resolve_registration_token)"' in source
    assert 'ROUTER_REGISTRATION_TOKEN="${ROUTER_REGISTRATION_TOKEN_VALUE}"' in source


def test_webterm_scripts_hard_stop_on_default_password_for_non_loopback_binds() -> None:
    dev_source = (_repo_root() / "scripts" / "dev_webterm_entry.sh").read_text(encoding="utf-8")
    tty_source = (_repo_root() / "scripts" / "tty_webterm_entry.sh").read_text(encoding="utf-8")
    assert "NETWORK_BIND_ADDR" in dev_source
    assert "NETWORK_BIND_ADDR" in tty_source
    assert "cannot remain default when NETWORK_BIND_ADDR=" in dev_source
    assert "cannot remain default when NETWORK_BIND_ADDR=" in tty_source
