from __future__ import annotations

from pathlib import Path

from auto_index_mcp.core.lsp import LspManager
from auto_index_mcp.core.service import AutoIndexService

from tests.lsp_fixtures import FakeProcessFactory


def test_lsp_start_restarts_sessions_when_project_root_changes(tmp_path: Path) -> None:
    first_project = tmp_path / "first"
    second_project = tmp_path / "second"
    first_project.mkdir()
    second_project.mkdir()
    (first_project / "main.cpp").write_text("int first()\n{\n    return 1;\n}\n", encoding="utf-8")
    (second_project / "main.cpp").write_text("int second()\n{\n    return 2;\n}\n", encoding="utf-8")

    factory = FakeProcessFactory()
    service = AutoIndexService(index_root=tmp_path / "index")
    service.lsp = LspManager(lambda name: f"/bin/{name}", factory)
    service.enable(str(first_project), rebuild=True)
    first = service.start_lsp(timeout_seconds=0.2)
    service.enable(str(second_project), rebuild=True)
    second = service.start_lsp(timeout_seconds=0.2)

    assert first.splitlines()[0] == f"LSP|ready|{first_project.as_posix()}"
    assert second.splitlines()[0] == f"LSP|ready|{second_project.as_posix()}"
    assert len(factory.processes) == 2
    assert factory.processes[0].cwd == str(first_project.resolve())
    assert factory.processes[0].returncode == 0
    assert factory.processes[1].cwd == str(second_project.resolve())


def test_lsp_start_stops_sessions_for_languages_no_longer_present(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    source = project / "main.cpp"
    source.write_text("int main()\n{\n    return 0;\n}\n", encoding="utf-8")

    factory = FakeProcessFactory()
    service = AutoIndexService(index_root=tmp_path / "index")
    service.lsp = LspManager(lambda name: f"/bin/{name}", factory)
    service.enable(str(project), rebuild=True)
    first = service.start_lsp(timeout_seconds=0.2)
    source.unlink()
    service.rebuild()
    second = service.start_lsp(timeout_seconds=0.2)

    assert first.splitlines()[0] == f"LSP|ready|{project.as_posix()}"
    assert second == f"LSP|no_targets|{project.as_posix()}"
    assert factory.processes[0].returncode == 0
    assert not service.lsp.sessions


def test_lsp_start_restarts_clangd_when_managed_compile_database_changes(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    first = project / "first.cpp"
    second = project / "second.cpp"
    first.write_text("int first()\n{\n    return 1;\n}\n", encoding="utf-8")

    factory = FakeProcessFactory()
    service = AutoIndexService(index_root=tmp_path / "index")
    service.lsp = LspManager(lambda name: f"/bin/{name}", factory)
    service.enable(str(project), rebuild=True)
    first_result = service.start_lsp(timeout_seconds=0.2)
    second.write_text("int second()\n{\n    return 2;\n}\n", encoding="utf-8")
    service.rebuild()
    second_result = service.start_lsp(timeout_seconds=0.2)

    assert first_result.splitlines()[0] == f"LSP|ready|{project.as_posix()}"
    assert second_result.splitlines()[0] == f"LSP|ready|{project.as_posix()}"
    assert len(factory.processes) == 2
    assert factory.processes[0].returncode == 0
    assert factory.processes[1].returncode is None


def test_lsp_start_restarts_clangd_when_managed_clangd_config_changes(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    source = project / "main.cpp"
    source.write_text("int main()\n{\n    return 0;\n}\n", encoding="utf-8")

    factory = FakeProcessFactory()
    service = AutoIndexService(index_root=tmp_path / "index")
    service.lsp = LspManager(lambda name: f"/bin/{name}", factory)
    service.enable(str(project), rebuild=True)
    first_result = service.start_lsp(timeout_seconds=0.2)
    (project / ".clangd").write_text("CompileFlags:\n  Add: [-Wall]\n", encoding="utf-8")
    second_result = service.start_lsp(timeout_seconds=0.2)

    assert first_result.splitlines()[0] == f"LSP|ready|{project.as_posix()}"
    assert second_result.splitlines()[0] == f"LSP|ready|{project.as_posix()}"
    assert len(factory.processes) == 2
    assert factory.processes[0].returncode == 0
    assert factory.processes[1].returncode is None
