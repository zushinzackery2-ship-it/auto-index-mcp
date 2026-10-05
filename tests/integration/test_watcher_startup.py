"""Watch startup remains asynchronous while its first snapshot settles."""
import time

from auto_index_mcp.application import watch_session
from auto_index_mcp.core.service import AutoIndexService


def test_start_watcher_returns_before_initial_snapshot_settles(tmp_path, write_file, monkeypatch):
    project = tmp_path / "project"
    write_file(project / "main.py", "print('ready')\n")
    service = AutoIndexService(index_root=tmp_path / "index")
    service.enable(str(project), rebuild=True)
    original = watch_session.take_watch_snapshot

    def slow_snapshot(*args, **kwargs):
        time.sleep(0.4)
        return original(*args, **kwargs)

    monkeypatch.setattr(watch_session, "take_watch_snapshot", slow_snapshot)
    try:
        started = time.perf_counter()
        service.start_watcher(debounce_seconds=0.1, wait_ready=False)
        assert time.perf_counter() - started < 0.2
        assert service.watcher_status()["running"]
        deadline = time.monotonic() + 5
        while not service.watcher_status()["ready"] and time.monotonic() < deadline:
            time.sleep(0.05)
        assert service.watcher_status()["ready"]
    finally:
        service.disable()
