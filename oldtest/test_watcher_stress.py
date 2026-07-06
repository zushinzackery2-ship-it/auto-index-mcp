from pathlib import Path
import random
import threading
import time

from auto_index_mcp.core.service import AutoIndexService


def test_concurrent_file_changes_handled_by_single_watcher(tmp_path: Path) -> None:
    project = tmp_path / "concurrent_writes"
    project.mkdir()
    (project / "seed.py").write_text("def seed():\n    pass\n", encoding="utf-8")

    service = AutoIndexService(index_root=tmp_path / "index")
    service.enable(str(project), rebuild=True)
    service.start_watcher(debounce_seconds=0.05)

    errors = []

    def writer(thread_id: int, count: int) -> None:
        try:
            for i in range(count):
                f = project / f"t{thread_id}_f{i}.py"
                f.write_text(
                    f"# thread {thread_id} file {i}\ndef t{thread_id}_f{i}():\n"
                    f"    return {thread_id * 1000 + i}\n",
                    encoding="utf-8",
                )
                time.sleep(random.uniform(0.001, 0.01))
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=writer, args=(i, 20)) for i in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    time.sleep(1.0)
    service.stop_watcher()

    assert not errors, f"Writer threads raised: {errors}"
    count = service.status()["file_count"]
    assert count >= 100, f"Expected >= 100 files, got {count}"


def test_watcher_and_rebuild_concurrent(tmp_path: Path) -> None:
    project = tmp_path / "watch_and_rebuild"
    project.mkdir()
    (project / "main.py").write_text("def main():\n    pass\n", encoding="utf-8")

    service = AutoIndexService(index_root=tmp_path / "index")
    service.enable(str(project), rebuild=True)
    service.start_watcher(debounce_seconds=0.05)

    errors = []

    def rebuild_loop() -> None:
        try:
            for _ in range(5):
                service.rebuild()
                time.sleep(0.1)
        except Exception as e:
            errors.append(e)

    def watcher_loop() -> None:
        try:
            for i in range(10):
                (project / f"change_{i}.py").write_text(f"def f{i}():\n    pass\n", encoding="utf-8")
                time.sleep(0.05)
        except Exception as e:
            errors.append(e)

    t1 = threading.Thread(target=rebuild_loop)
    t2 = threading.Thread(target=watcher_loop)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    service.stop_watcher()

    assert not errors, f"Concurrent ops raised: {errors}"


def test_watcher_100_add_remove_cycles(tmp_path: Path) -> None:
    project = tmp_path / "cycle_stress"
    project.mkdir()
    (project / "anchor.py").write_text("def anchor():\n    pass\n", encoding="utf-8")

    service = AutoIndexService(index_root=tmp_path / "index")
    service.enable(str(project), rebuild=True)
    service.start_watcher(debounce_seconds=0.05)

    try:
        for i in range(100):
            f = project / f"cycle_{i}.py"
            f.write_text(f"def cycle_{i}():\n    pass\n", encoding="utf-8")
            time.sleep(0.01)
            if i % 2 == 0:
                _unlink_with_retry(f)

        assert _wait_until(lambda: service.status()["file_count"] == 51, timeout=10.0)
        assert service.watcher_status()["change_count"] >= 1
    finally:
        service.stop_watcher()


def test_watcher_modify_same_file_500_times(tmp_path: Path) -> None:
    project = tmp_path / "modify_stress"
    project.mkdir()
    f = project / "volatile.py"
    f.write_text("v = 0\n", encoding="utf-8")

    service = AutoIndexService(index_root=tmp_path / "index")
    service.enable(str(project), rebuild=True)
    service.start_watcher(debounce_seconds=0.05)

    try:
        for i in range(500):
            f.write_text(f"v = {i}\n", encoding="utf-8")
            time.sleep(0.002)

        time.sleep(1.0)
        content = service.file_content("volatile.py")
        assert "v = " in content
        status = service.watcher_status()
        assert status["change_count"] >= 1
    finally:
        service.stop_watcher()


def test_snapshot_fingerprint_changes_on_file_content_change(tmp_path: Path) -> None:
    project = tmp_path / "fingerprint_stress"
    project.mkdir()
    f = project / "main.py"
    f.write_text("def main():\n    pass\n", encoding="utf-8")

    service = AutoIndexService(index_root=tmp_path / "index")
    service.enable(str(project), rebuild=True)
    service.start_watcher(debounce_seconds=0.05)

    try:
        f.write_text("def main():\n    return True\n", encoding="utf-8")
        assert _wait_until(
            lambda: (service.watcher_status().get("last_result") or {}).get("modified", 0) >= 1
        )
    finally:
        service.stop_watcher()


def _wait_until(predicate, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def _unlink_with_retry(path: Path, timeout: float = 2.0) -> None:
    deadline = time.time() + timeout
    while True:
        try:
            path.unlink()
            return
        except PermissionError:
            if time.time() >= deadline:
                raise
            time.sleep(0.02)
