import os
import time

from auto_index_mcp.application.watch_session import WatchSession
from auto_index_mcp.core.service import AutoIndexService
from auto_index_mcp.indexing.snapshot import WatchSnapshot


def test_event_delta_does_not_iterate_unaffected_file_map():
    class PointLookupOnly(dict):
        def __iter__(self):
            raise AssertionError("event delta iterated the full project")

    previous = WatchSnapshot(PointLookupOnly(a=(1, 1), b=(2, 2)), dict())
    current = WatchSnapshot(PointLookupOnly(a=(1, 3), b=(2, 2)), dict(), changed_paths=frozenset(["a"]))
    assert current.changed_files(previous) == ([], [], ["a"])


def replace_same_stat(path, name, timestamp):
    path.write_text(f"def {name}():\n    return 1\n", encoding="utf-8")
    os.utime(path, ns=(timestamp, timestamp))


def test_explicit_same_stat_event_checks_source_content(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    source = project / "main.py"
    source.write_text("def name_old():\n    return 1\n", encoding="utf-8")
    timestamp = source.stat().st_mtime_ns
    service = AutoIndexService(tmp_path / "index")
    service.enable(str(project))
    try:
        session = WatchSession(project, service.store, service.rebuild_sync)
        previous = session.baseline()
        replace_same_stat(source, "name_new", timestamp)
        current = session.update_snapshot(previous, {source})
        assert current.files == previous.files
        result = session.apply(previous, current)
        assert result["rewritten"] == 1
        assert service.symbol_search("name_new")["items"]
        unchanged = session.update_snapshot(current, {source})
        assert session.apply(current, unchanged)["rewritten"] == 0
    finally:
        service.disable()


def test_consecutive_same_stat_watcher_events_are_published(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    source = project / "main.py"
    source.write_text("def name_old():\n    return 1\n", encoding="utf-8")
    timestamp = source.stat().st_mtime_ns
    service = AutoIndexService(tmp_path / "index")
    service.enable(str(project))
    try:
        service.start_watcher(debounce_seconds=0.05, wait_ready=True)
        for name in ("name_new", "name_end"):
            replace_same_stat(source, name, timestamp)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not service.symbol_search(name)["items"]:
                time.sleep(0.02)
            assert service.symbol_search(name)["items"]
    finally:
        service.disable()
