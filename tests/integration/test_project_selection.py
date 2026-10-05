"""Project selection is explicit when roots or shared transports are involved."""
from types import SimpleNamespace

import anyio
import pytest

from auto_index_mcp.core.service import AutoIndexService
from auto_index_mcp.mcp_api import server
from auto_index_mcp.mcp_api.bootstrap import ensure_enabled
from auto_index_mcp.mcp_api.guard import run_tool


def context_for(roots):
    async def list_roots():
        return SimpleNamespace(roots=[SimpleNamespace(uri=root.as_uri()) for root in roots])
    return SimpleNamespace(session=SimpleNamespace(list_roots=list_roots))


def test_multiple_client_roots_require_explicit_selection(tmp_path, write_file, monkeypatch):
    monkeypatch.delenv("AUTO_INDEX_PROJECT_PATH", raising=False)
    roots = [tmp_path / "first", tmp_path / "second"]
    for root in roots:
        write_file(root / "main.py", "def active():\n    pass\n")
    service = AutoIndexService()
    try:
        result = anyio.run(ensure_enabled, service, context_for(roots))
        assert result["error"] == "ambiguous-project"
        assert set(result["roots"]) == set(str(root.resolve()) for root in roots)
        assert not service.enabled
    finally:
        service.disable()


def test_client_root_order_does_not_override_explicit_attachment(tmp_path, write_file):
    roots = [tmp_path / "first", tmp_path / "second"]
    for root in roots:
        write_file(root / "main.py", "def active():\n    pass\n")
    service = AutoIndexService()
    try:
        service.enable(str(roots[1]), refresh_embedder=False)
        assert anyio.run(ensure_enabled, service, context_for(roots)) is None
        assert service.root_path == roots[1].resolve()
    finally:
        service.disable()


def test_http_server_rejects_switching_the_shared_project(tmp_path, write_file, monkeypatch):
    first, second = tmp_path / "first", tmp_path / "second"
    write_file(first / "main.py", "def first_only():\n    pass\n")
    write_file(second / "main.py", "def second_only():\n    pass\n")
    service = AutoIndexService()
    service.semantic_enabled = False
    seen = []

    def run(transport):
        assert transport == "streamable-http"
        result = run_tool(service.enable_reusing_index, str(second))
        seen.append(result)
        assert result["error"] == "project-fixed"
        assert service.root_path == first.resolve()
        if service.background is not None:
            assert service.background.wait(15)
        assert service.symbol_body("first_only")["path"] == "main.py"

    monkeypatch.setattr(server, "_service", service)
    monkeypatch.setattr(server, "mcp", SimpleNamespace(settings=SimpleNamespace(port=None), run=run))
    monkeypatch.setattr(server, "_register_shutdown_hooks", lambda: None)
    server.main(["--transport", "streamable-http", "--project-path", str(first), "--no-watch"])
    assert len(seen) == 1
    assert not service.enabled


def test_http_requires_a_fixed_project_before_listening(monkeypatch):
    monkeypatch.delenv("AUTO_INDEX_PROJECT_PATH", raising=False)
    monkeypatch.setattr(server, "_service", AutoIndexService())
    monkeypatch.setattr(server, "_register_shutdown_hooks", lambda: None)
    monkeypatch.setattr(server, "mcp", SimpleNamespace(run=lambda **kwargs: pytest.fail("server listened without a project")))
    with pytest.raises(ValueError, match="project"):
        server.main(["--transport", "sse", "--no-watch"])
