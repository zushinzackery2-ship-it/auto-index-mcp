from pathlib import Path
import time
from typing import Callable

from auto_index_mcp.core.service import AutoIndexService


def test_watcher_incrementally_updates_changed_file_without_rebuild(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    source = project / "main.py"
    source.write_text("def old_name():\n    return True\n", encoding="utf-8")

    service = AutoIndexService(index_root=tmp_path / "index")
    service.enable(str(project), rebuild=True)
    service.start_watcher(debounce_seconds=0.1)

    try:
        status = service.watcher_status()
        assert status["mode"] == "filesystem-events"
        assert status["debounce_seconds"] == 0.1

        source.write_text("def new_name():\n    return False\n", encoding="utf-8")

        assert _wait_until(
            lambda: _has_symbol(service, "main.py", "new_name")
            and service.watcher_status().get("last_result", {}).get("status") == "incremental"
        )
        result = service.watcher_status()["last_result"]
        assert result["rebuild"] is False
        assert result["modified"] == 1
        assert not _has_symbol(service, "main.py", "old_name")
    finally:
        service.stop_watcher()


def test_watcher_incrementally_adds_new_file_without_rebuild(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("print('ready')\n", encoding="utf-8")

    service = AutoIndexService(index_root=tmp_path / "index")
    service.enable(str(project), rebuild=True)
    service.start_watcher(debounce_seconds=0.1)

    try:
        (project / "new_file.py").write_text("def created():\n    return True\n", encoding="utf-8")

        assert _wait_until(lambda: service.resolve_path("new_file.py")["items"])
        result = service.watcher_status()["last_result"]
        assert result["status"] == "incremental"
        assert result["rebuild"] is False
        assert result["added"] == 1
        assert service.watcher_status()["change_count"] >= 1
    finally:
        service.stop_watcher()


def test_watcher_incrementally_deletes_file_without_rebuild(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    doomed = project / "remove_me.py"
    doomed.write_text("def remove_me():\n    return True\n", encoding="utf-8")

    service = AutoIndexService(index_root=tmp_path / "index")
    service.enable(str(project), rebuild=True)
    service.start_watcher(debounce_seconds=0.1)

    try:
        doomed.unlink()

        assert _wait_until(lambda: not service.resolve_path("remove_me.py")["items"])
        result = service.watcher_status()["last_result"]
        assert result["status"] == "incremental"
        assert result["rebuild"] is False
        assert result["deleted"] == 1
    finally:
        service.stop_watcher()


def test_watcher_removes_record_when_file_becomes_unindexable(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    source = project / "large.py"
    source.write_text("def once_indexed():\n    return True\n", encoding="utf-8")

    service = AutoIndexService(index_root=tmp_path / "index")
    service.enable(str(project), rebuild=True)
    service.start_watcher(debounce_seconds=0.1)

    try:
        source.write_text("x = '" + ("a" * 2_000_001) + "'\n", encoding="utf-8")

        assert _wait_until(lambda: not service.resolve_path("large.py")["items"])
        result = service.watcher_status()["last_result"]
        assert result["status"] == "incremental"
        assert result["rebuild"] is False
        assert result["modified"] == 1
    finally:
        service.stop_watcher()


def test_watcher_slims_parent_when_child_index_appears(tmp_path: Path) -> None:
    project = tmp_path / "project"
    child = project / "child"
    child.mkdir(parents=True)
    (project / "root.py").write_text("def root_only():\n    return True\n", encoding="utf-8")
    (child / "child.py").write_text("def child_only():\n    return True\n", encoding="utf-8")

    parent_service = AutoIndexService()
    parent_service.enable(str(project), rebuild=True)

    assert parent_service.status()["file_count"] == 2
    assert [item["path"] for item in parent_service.store.all_files()] == ["child/child.py", "root.py"]

    parent_service.start_watcher(debounce_seconds=0.1)
    try:
        child_service = AutoIndexService()
        child_service.enable(str(child), rebuild=True)

        assert _wait_until(
            lambda: parent_service.status()["file_count"] == 1
            and parent_service.status()["child_index_count"] == 1
        )
        result = parent_service.watcher_status()["last_result"]
        assert result["update_mode"] == "structural-rebuild"
        assert [item["path"] for item in parent_service.store.all_files()] == ["root.py"]
        assert parent_service.resolve_path("child.py")["items"][0]["path"] == "child/child.py"
    finally:
        parent_service.stop_watcher()


def test_watcher_refreshes_child_link_metadata_without_parent_rebuild(tmp_path: Path) -> None:
    project = tmp_path / "project"
    child = project / "child"
    child.mkdir(parents=True)
    (project / "root.py").write_text("def root_only():\n    return True\n", encoding="utf-8")
    (child / "child.py").write_text("def child_only():\n    return True\n", encoding="utf-8")

    child_service = AutoIndexService()
    child_service.enable(str(child), rebuild=True)
    parent_service = AutoIndexService()
    parent_service.enable(str(project), rebuild=True)
    parent_service.start_watcher(debounce_seconds=0.1)

    try:
        (child / "extra.py").write_text("def extra_only():\n    return True\n", encoding="utf-8")
        child_service.rebuild()

        assert _wait_until(
            lambda: parent_service.status()["total_file_count"] == 3
            and parent_service.resolve_path("extra.py")["items"]
        )
        result = parent_service.watcher_status()["last_result"]
        assert result["status"] == "metadata-refresh"
        assert result["rebuild"] is False
        assert result["child_indexes_modified"] == 1
    finally:
        parent_service.stop_watcher()


def _wait_until(predicate: Callable[[], bool], timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return False


def _has_symbol(service: AutoIndexService, path: str, symbol_name: str) -> bool:
    return any(symbol["name"] == symbol_name for symbol in service.file_summary(path)["symbols"])
