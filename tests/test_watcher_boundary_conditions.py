from pathlib import Path
import time

from auto_index_mcp.core.service import AutoIndexService


def test_watcher_handles_rapid_successive_changes(tmp_path: Path) -> None:
    project = tmp_path / "rapid_changes"
    project.mkdir()
    f = project / "rapid.py"
    f.write_text("v = 0\n", encoding="utf-8")

    service = AutoIndexService(index_root=tmp_path / "index")
    service.enable(str(project), rebuild=True)
    service.start_watcher(debounce_seconds=0.05)

    try:
        for i in range(20):
            f.write_text(f"v = {i}\n", encoding="utf-8")
            time.sleep(0.01)

        time.sleep(0.5)
        service.file_summary("rapid.py")
        content = service.file_content("rapid.py")
        assert "v = " in content
    finally:
        service.stop_watcher()


def test_watcher_file_created_and_deleted_before_ready(tmp_path: Path) -> None:
    project = tmp_path / "ephemeral"
    project.mkdir()
    (project / "main.py").write_text("def main():\n    pass\n", encoding="utf-8")

    service = AutoIndexService(index_root=tmp_path / "index")
    service.enable(str(project), rebuild=True)

    service.start_watcher(debounce_seconds=0.05)
    try:
        ephemeral = project / "ephemeral.py"
        ephemeral.write_text("def gone():\n    pass\n", encoding="utf-8")
        ephemeral.unlink()

        assert service.watcher_status()["ready"] is True
        assert service.resolve_path("ephemeral.py")["items"] == []
    finally:
        service.stop_watcher()


def test_watcher_handles_100_rapid_file_adds(tmp_path: Path) -> None:
    project = tmp_path / "many_adds"
    project.mkdir()
    (project / "main.py").write_text("def main():\n    pass\n", encoding="utf-8")

    service = AutoIndexService(index_root=tmp_path / "index")
    service.enable(str(project), rebuild=True)
    service.start_watcher(debounce_seconds=0.1)

    try:
        for i in range(100):
            (project / f"file_{i:03d}.py").write_text(f"def f{i}():\n    pass\n", encoding="utf-8")

        assert _wait_until(lambda: service.status()["file_count"] == 101, timeout=10)
        assert service.watcher_status()["change_count"] >= 1
    finally:
        service.stop_watcher()


def _wait_until(predicate, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False
