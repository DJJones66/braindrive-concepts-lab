from __future__ import annotations

from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def test_env_example_uses_single_network_exposure_switch_with_default_false():
    source = (_repo_root() / ".env.example").read_text(encoding="utf-8")
    assert "NETWORK_EXPOSED=false" in source
    assert "NETWORK_BIND_ADDR=127.0.0.1" in source


def test_compose_port_publishing_uses_derived_network_bind_addr():
    source = (_repo_root() / "docker-compose.yml").read_text(encoding="utf-8")
    expected_mappings = [
        '${NETWORK_BIND_ADDR:-127.0.0.1}:${BRAINDRIVE_ROUTER_PORT:-9480}:8080',
        '${NETWORK_BIND_ADDR:-127.0.0.1}:${BRAINDRIVE_INTENT_PORT:-9481}:8081',
        '${NETWORK_BIND_ADDR:-127.0.0.1}:${BRAINDRIVE_GATEWAY_PORT:-9482}:8090',
        '${NETWORK_BIND_ADDR:-127.0.0.1}:${BRAINDRIVE_WEBCONSOLE_PORT:-9493}:7681',
        '${NETWORK_BIND_ADDR:-127.0.0.1}:${BRAINDRIVE_DEV_WEBTERM_PORT:-9494}:7681',
    ]
    for mapping in expected_mappings:
        assert mapping in source


def test_bootstrap_derives_network_bind_addr_from_network_exposed():
    source = (_repo_root() / "scripts" / "bootstrap.sh").read_text(encoding="utf-8")
    assert "resolve_boolean_env()" in source
    assert 'NETWORK_EXPOSED_VALUE="$(resolve_boolean_env "NETWORK_EXPOSED" "false")"' in source
    assert 'NETWORK_BIND_ADDR_VALUE="0.0.0.0"' in source
    assert 'NETWORK_BIND_ADDR_VALUE="127.0.0.1"' in source
    assert 'NETWORK_BIND_ADDR="${NETWORK_BIND_ADDR_VALUE}"' in source


def test_webterm_services_receive_provider_env_for_cli_streaming():
    source = (_repo_root() / "docker-compose.yml").read_text(encoding="utf-8")
    assert source.count('BRAINDRIVE_OPENROUTER_API_KEY: "${BRAINDRIVE_OPENROUTER_API_KEY:-}"') >= 2
    assert source.count('BRAINDRIVE_OPENROUTER_DEFAULT_MODEL: "${BRAINDRIVE_OPENROUTER_DEFAULT_MODEL:-}"') >= 2
    assert source.count('BRAINDRIVE_OLLAMA_API_KEY: "${BRAINDRIVE_OLLAMA_API_KEY:-}"') >= 2
