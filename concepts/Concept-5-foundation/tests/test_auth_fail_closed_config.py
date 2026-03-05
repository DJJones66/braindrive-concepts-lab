from __future__ import annotations

from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def test_env_example_defaults_to_fail_closed_auth_mode() -> None:
    source = (_repo_root() / ".env.example").read_text(encoding="utf-8")
    assert "GATEWAY_ENFORCE_SESSION=true" in source


def test_compose_defaults_to_fail_closed_auth_mode() -> None:
    source = (_repo_root() / "docker-compose.yml").read_text(encoding="utf-8")
    assert 'GATEWAY_ENFORCE_SESSION: "${GATEWAY_ENFORCE_SESSION:-true}"' in source


def test_gateway_adapter_auth_mode_surface_is_session_enforced() -> None:
    source = (_repo_root() / "services" / "gateway_adapter_service.py").read_text(encoding="utf-8")
    assert 'ENFORCE_SESSION = _env_bool("GATEWAY_ENFORCE_SESSION", True)' in source
    assert "gateway.api auth mode:" in source
