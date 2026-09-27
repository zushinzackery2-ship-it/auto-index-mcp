"""Cold-start bootstrap: env-var root resolution, lazy auto-enable, hints."""

from __future__ import annotations

from pathlib import Path

import anyio

from auto_index_mcp.core.service import AutoIndexService
from auto_index_mcp.mcp_api.bootstrap import (
    PROJECT_PATH_ENV,
    ensure_enabled,
    uri_to_path,
)

CODE_PY = "def compute(rows):\n    return sum(rows)\n"


def _ensure(service: AutoIndexService):
    async def runner():
        return await ensure_enabled(service, None)

    return anyio.run(runner)


def test_ensure_enabled_noops_when_already_attached(tmp_path: Path, write_file, make_service) -> None:
    project = tmp_path / "proj"
    write_file(project / "mod.py", CODE_PY)
    service = make_service(project)
    assert _ensure(service) is None


def test_ensure_enabled_uses_env_root(tmp_path: Path, write_file, monkeypatch) -> None:
    project = tmp_path / "proj"
    write_file(project / "mod.py", CODE_PY)
    monkeypatch.setenv(PROJECT_PATH_ENV, str(project))
    service = AutoIndexService(index_root=tmp_path / ".idx")
    try:
        assert _ensure(service) is None
        assert service.root_path == project.resolve()
        if service.background is not None:
            assert service.background.wait(15.0) is True
        assert service.overview()["file_count"] >= 1
    finally:
        service.disable()


def test_ensure_enabled_reports_structured_error_without_any_root(monkeypatch) -> None:
    monkeypatch.delenv(PROJECT_PATH_ENV, raising=False)
    service = AutoIndexService()
    result = _ensure(service)
    assert result is not None
    assert result["error"] == "not-enabled"
    assert "auto_index_enable" in result["hint"]


def test_env_root_must_exist(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(PROJECT_PATH_ENV, str(tmp_path / "missing"))
    service = AutoIndexService()
    result = _ensure(service)
    assert result is not None and result["error"] == "not-enabled"


def test_uri_to_path_rejects_non_file_schemes() -> None:
    assert uri_to_path("https://example.com/x") is None


def test_uri_to_path_decodes_file_uri(tmp_path: Path) -> None:
    uri = tmp_path.as_uri()
    resolved = uri_to_path(uri)
    assert resolved is not None
    assert resolved.resolve() == tmp_path.resolve()


def test_server_declares_instructions() -> None:
    from auto_index_mcp.mcp_api.server import mcp

    assert mcp.instructions and "auto_index_enable" in mcp.instructions
