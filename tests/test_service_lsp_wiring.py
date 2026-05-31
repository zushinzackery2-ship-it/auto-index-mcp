from pathlib import Path

import auto_index_mcp.lsp.manager as manager_module
from auto_index_mcp.core.service import AutoIndexService


def test_service_lsp_reports_unavailable_without_servers(tmp_path: Path, monkeypatch) -> None:
    # No real language servers should ever be spawned in tests.
    monkeypatch.setattr(manager_module, "resolve_lsp_executable", lambda name, root=None: None)

    project = tmp_path / "project"
    project.mkdir()
    (project / "a.py").write_text("x = 1\n", encoding="utf-8")

    service = AutoIndexService(index_root=tmp_path / "index")
    service.enable(str(project), rebuild=True)

    start = service.start_lsp(2.0, background=False)
    assert "unavailable" in start and "pyright" in start

    check = service.check_lsp(timeout_seconds=1.0)
    assert check.startswith("CHK|")  # not_started / unavailable, never a crash or hang
    assert service.stop_lsp(1.0).startswith("LSP|stopped")


def test_service_lsp_check_before_enable_raises(tmp_path: Path) -> None:
    service = AutoIndexService(index_root=tmp_path / "index")
    try:
        service.check_lsp()
    except RuntimeError:
        return
    raise AssertionError("check_lsp must require an enabled index")
