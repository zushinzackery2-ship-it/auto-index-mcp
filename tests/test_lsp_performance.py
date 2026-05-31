from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from auto_index_mcp.compatibility.code_index import CompatService
from auto_index_mcp.core import lsp as lsp_module
from auto_index_mcp.core.clangd_bootstrap import ClangdBootstrap
from auto_index_mcp.core.lsp import LspManager
from auto_index_mcp.core.service import AutoIndexService

from tests.lsp_fixtures import FakeProcessFactory, HangingProcessFactory


class RecordingSession:
    def __init__(self) -> None:
        self.diagnostics: dict[str, list[dict[str, Any]]] = {}
        self.opened: list[str] = []
        self.waited = 0.0

    def is_running(self) -> bool:
        return True

    def open_document(self, uri: str, language_id: str, version: int, text: str, workspace_signature: str = "") -> None:
        _ = language_id, version, text, workspace_signature
        self.opened.append(uri)

    def wait_for_diagnostics(self, uris: set[str], timeout_seconds: float) -> None:
        _ = uris
        self.waited += timeout_seconds


def test_compat_set_project_path_reuses_existing_index_without_rebuild(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("def ready():\n    return True\n", encoding="utf-8")

    first_service = AutoIndexService()
    CompatService(first_service).set_project_path(str(project))
    (project / "created_after_first_index.py").write_text("def later():\n    return True\n", encoding="utf-8")
    second_service = AutoIndexService()

    def fail_rebuild() -> dict[str, Any]:
        raise AssertionError("existing project index should be reused")

    monkeypatch.setattr(second_service, "rebuild", fail_rebuild)

    compat = CompatService(second_service)
    result = compat.set_project_path(str(project))

    assert "Indexed 2 total files (2 local)" in result
    assert compat.find_files("created_after_first_index.py") == ["created_after_first_index.py"]


def test_start_watcher_is_idempotent_for_same_root_and_debounce(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("print('ready')\n", encoding="utf-8")

    service = AutoIndexService(index_root=tmp_path / "index")
    service.enable(str(project), rebuild=True)
    service.start_watcher(debounce_seconds=0.1)

    try:
        first_watcher = service.watcher
        service.start_watcher(debounce_seconds=0.1)

        assert service.watcher is first_watcher
    finally:
        service.stop_watcher()


def test_lsp_start_reuses_cached_clangd_bootstrap_for_unchanged_inputs(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.cpp").write_text("int main()\n{\n    return 0;\n}\n", encoding="utf-8")
    calls = 0

    def fake_prepare(root: Path, files: list[dict[str, Any]]) -> ClangdBootstrap:
        nonlocal calls
        _ = root, files
        calls += 1
        return ClangdBootstrap((), ("ccdb=fake",), frozenset({"main.cpp"}), "stable")

    factory = FakeProcessFactory()
    service = AutoIndexService(index_root=tmp_path / "index")
    service.lsp = LspManager(lambda *args: f"/bin/{args[0]}", factory)
    monkeypatch.setattr(lsp_module, "prepare_clangd", fake_prepare)
    service.enable(str(project), rebuild=True)

    service.start_lsp(timeout_seconds=0.2)
    service.start_lsp(timeout_seconds=0.2)

    assert calls == 1
    assert len(factory.processes) == 1


def test_repeated_set_project_path_on_active_root_returns_status_without_sync(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("def ready():\n    return True\n", encoding="utf-8")

    service = AutoIndexService(index_root=tmp_path / "index")
    compat = CompatService(service)
    compat.set_project_path(str(project))
    (project / "created_after_active_root.py").write_text("def later():\n    return True\n", encoding="utf-8")

    def fail_sync() -> dict[str, Any]:
        raise AssertionError("active-root repeated set_project_path should not synchronously catch up")

    monkeypatch.setattr(service, "sync_index_to_filesystem", fail_sync)
    started = time.perf_counter()
    result = compat.set_project_path(str(project))
    elapsed = time.perf_counter() - started

    assert "Indexed 1 total files (1 local)" in result
    assert elapsed < 0.1


def test_lsp_start_async_returns_quickly_while_server_initialization_hangs(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("value = 1\n", encoding="utf-8")

    factory = HangingProcessFactory()
    service = AutoIndexService(index_root=tmp_path / "index")
    service.lsp = LspManager(lambda name: f"/bin/{name}", factory)
    service.enable(str(project), rebuild=True)

    started = time.perf_counter()
    first = service.start_lsp(timeout_seconds=5.0, background=True)
    second = service.start_lsp(timeout_seconds=5.0, background=True)
    elapsed = time.perf_counter() - started

    assert first.startswith(f"LSP|starting|{project.as_posix()}")
    assert second.startswith(f"LSP|starting|{project.as_posix()}")
    assert elapsed < 0.2
    assert len(factory.processes) == 1


def test_lsp_start_async_returns_ready_after_background_start_completes(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("value = 1\n", encoding="utf-8")

    factory = FakeProcessFactory()
    service = AutoIndexService(index_root=tmp_path / "index")
    service.lsp = LspManager(lambda name: f"/bin/{name}", factory)
    service.enable(str(project), rebuild=True)

    first = service.start_lsp(timeout_seconds=0.2, background=True)
    deadline = time.perf_counter() + 1.0
    ready = ""
    while time.perf_counter() < deadline:
        ready = service.start_lsp(timeout_seconds=0.2, background=True)
        if ready.startswith("LSP|ready|"):
            break
        time.sleep(0.01)

    assert first.startswith(f"LSP|starting|{project.as_posix()}")
    assert ready.startswith(f"LSP|ready|{project.as_posix()}")
    assert len(factory.processes) == 1


def test_lsp_check_stops_opening_targets_when_timeout_budget_expires(tmp_path: Path) -> None:
    manager = LspManager()
    session = RecordingSession()
    manager.sessions["pyright"] = session
    files = [
        {
            "path": f"file_{index}.py",
            "extension": ".py",
            "language": "python",
            "mtime_ns": index + 1,
            "size": 1,
            "sha1": str(index),
        }
        for index in range(50)
    ]

    def slow_reader(item: dict[str, Any]) -> tuple[str, str]:
        time.sleep(0.01)
        return "print('x')\n", (tmp_path / item["path"]).as_uri()

    started = time.perf_counter()
    result = manager.check(tmp_path, files, slow_reader, timeout_seconds=0.05)
    elapsed = time.perf_counter() - started

    assert result.startswith("CHK|partial|")
    assert len(session.opened) < len(files)
    assert elapsed < 0.3


def test_explicit_lsp_check_does_not_read_full_workspace(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "project"
    project.mkdir()
    source = project / "main.py"
    source.write_text("value = 1\n", encoding="utf-8")

    service = AutoIndexService(index_root=tmp_path / "index")
    service.enable(str(project), rebuild=True)
    service.lsp.sessions["pyright"] = RecordingSession()

    def fail_all_files() -> list[dict[str, Any]]:
        raise AssertionError("single-file LSP check should not enumerate the full workspace")

    monkeypatch.setattr(service.store, "all_files", fail_all_files)

    result = service.check_lsp(path="main.py", timeout_seconds=0.1)

    assert result == "CHK|partial|files=0|unchecked=1"


def test_lsp_check_returns_starting_while_background_start_is_active(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("value = 1\n", encoding="utf-8")

    factory = HangingProcessFactory()
    service = AutoIndexService(index_root=tmp_path / "index")
    service.lsp = LspManager(lambda name: f"/bin/{name}", factory)
    service.enable(str(project), rebuild=True)
    service.start_lsp(timeout_seconds=5.0, background=True)

    started = time.perf_counter()
    result = service.check_lsp(path="main.py", timeout_seconds=0.1)
    elapsed = time.perf_counter() - started

    assert result.startswith(f"CHK|starting|{project.as_posix()}")
    assert elapsed < 0.2


def test_lsp_check_bounds_slow_single_file_reader(tmp_path: Path) -> None:
    manager = LspManager()
    manager.sessions["pyright"] = RecordingSession()
    files = [
        {
            "path": "main.py",
            "extension": ".py",
            "language": "python",
            "mtime_ns": 1,
            "size": 1,
            "sha1": "1",
        }
    ]

    def slow_reader(item: dict[str, Any]) -> tuple[str, str]:
        _ = item
        time.sleep(1.0)
        return "value = 1\n", (tmp_path / "main.py").as_uri()

    started = time.perf_counter()
    result = manager.check(tmp_path, files, slow_reader, path="main.py", timeout_seconds=0.1)
    elapsed = time.perf_counter() - started

    assert result == "CHK|partial|files=0|unchecked=1"
    assert elapsed < 0.5


def test_lsp_check_bounds_slow_open_document(tmp_path: Path) -> None:
    class SlowOpenSession(RecordingSession):
        def open_document(self, uri: str, language_id: str, version: int, text: str, workspace_signature: str = "") -> None:
            _ = uri, language_id, version, text, workspace_signature
            time.sleep(1.0)

    manager = LspManager()
    manager.sessions["pyright"] = SlowOpenSession()
    files = [
        {
            "path": "main.py",
            "extension": ".py",
            "language": "python",
            "mtime_ns": 1,
            "size": 1,
            "sha1": "1",
        }
    ]

    def reader(item: dict[str, Any]) -> tuple[str, str]:
        return "value = 1\n", (tmp_path / item["path"]).as_uri()

    started = time.perf_counter()
    result = manager.check(tmp_path, files, reader, path="main.py", timeout_seconds=0.1)
    elapsed = time.perf_counter() - started

    assert result == "CHK|partial|files=0|unchecked=1"
    assert elapsed < 0.5
