from __future__ import annotations

from pathlib import Path

from braindrive_runtime.debug_server import DebugIntentServer
from braindrive_runtime.runtime import BrainDriveRuntime


def test_debug_endpoints_enabled(runtime):
    analyze = runtime.test_endpoint("/intent/analyze", {"message": "list folders"})
    assert analyze["ok"] is True
    assert "analysis" in analyze

    catalog = runtime.test_endpoint("/intent/capabilities", {})
    assert catalog["ok"] is True
    assert "catalog" in catalog


def test_debug_endpoints_disabled(tmp_path: Path):
    rt = BrainDriveRuntime(
        library_root=tmp_path / "library",
        data_root=tmp_path / "runtime-data",
        env={
            "BRAINDRIVE_ENABLE_TEST_ENDPOINTS": "false",
            "BRAINDRIVE_DEFAULT_PROVIDER": "openrouter",
            "BRAINDRIVE_OPENROUTER_API_KEY": "test-key",
            "BRAINDRIVE_OPENROUTER_DEFAULT_MODEL": "anthropic/claude-sonnet-4",
            "BRAINDRIVE_OLLAMA_BASE_URL": "http://localhost:11434/v1",
            "BRAINDRIVE_OLLAMA_DEFAULT_MODEL": "llama3:8b",
        },
    )
    resp = rt.test_endpoint("/intent/analyze", {"message": "hello"})
    assert resp["ok"] is False


def test_debug_server_defaults_to_loopback(runtime):
    server = DebugIntentServer(runtime)
    assert server.host == "127.0.0.1"
