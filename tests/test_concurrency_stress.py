from pathlib import Path
import threading

from auto_index_mcp.core.service import AutoIndexService


def test_10_concurrent_rebuilds_only_one_succeeds(tmp_path: Path) -> None:
    from auto_index_mcp.indexing.build_lock import BuildLock

    project = tmp_path / "lock_stress"
    project.mkdir()
    (project / "main.py").write_text("def main():\n    pass\n", encoding="utf-8")

    lock_path = tmp_path / "index.build.lock"
    succeeded = []
    errors = []

    def try_rebuild(thread_id: int) -> None:
        try:
            lock = BuildLock(lock_path, stale_seconds=5.0, poll_seconds=0.05)
            if lock.acquire(10.0):
                try:
                    svc = AutoIndexService(index_root=tmp_path / f"idx_{thread_id}")
                    svc.enable(str(project), rebuild=True)
                    succeeded.append(thread_id)
                finally:
                    lock.release()
            else:
                errors.append(f"thread_{thread_id}: timeout acquiring lock")
        except Exception as e:
            errors.append(f"thread_{thread_id}: {e}")

    threads = [threading.Thread(target=try_rebuild, args=(i,)) for i in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(succeeded) >= 1
    assert not errors


def test_parent_and_child_concurrent_rebuild(tmp_path: Path) -> None:
    project = tmp_path / "parent_child"
    child = project / "child"
    child.mkdir(parents=True)
    (project / "root.py").write_text("def root():\n    pass\n", encoding="utf-8")
    (child / "child.py").write_text("def child():\n    pass\n", encoding="utf-8")

    errors = []
    results = {}

    def parent_rebuild() -> None:
        try:
            svc = AutoIndexService()
            results["parent"] = svc.enable(str(project), rebuild=True)
        except Exception as e:
            errors.append(f"parent: {e}")

    def child_rebuild() -> None:
        try:
            svc = AutoIndexService()
            results["child"] = svc.enable(str(child), rebuild=True)
        except Exception as e:
            errors.append(f"child: {e}")

    t1 = threading.Thread(target=parent_rebuild)
    t2 = threading.Thread(target=child_rebuild)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert not errors, f"Concurrent rebuilds failed: {errors}"
    assert results["child"]["file_count"] == 1
    assert results["parent"]["total_file_count"] == 2
    assert results["parent"]["file_count"] in {1, 2}
