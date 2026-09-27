from __future__ import annotations

import threading

from auto_index_mcp.core.service import AutoIndexService
from auto_index_mcp.runtime.background import BackgroundIndexer
from auto_index_mcp.runtime.leases import BuildLock


def test_cancelled_delayed_worker_never_runs_work_or_callback():
    calls = []
    worker = BackgroundIndexer(lambda bg: calls.append("work"), on_done=lambda result: calls.append("done"))
    worker.start(delay_seconds=60)
    worker.cancelled.set()
    assert worker.join(2)
    assert worker.wait(0)
    assert worker.status()["state"] == "cancelled"
    assert calls == []


def test_wait_includes_completion_callback():
    entered, release = threading.Event(), threading.Event()

    def callback(result):
        entered.set()
        assert release.wait(2)

    worker = BackgroundIndexer(lambda bg: dict(status="indexed"), on_done=callback)
    worker.start()
    try:
        assert entered.wait(2)
        assert not worker.wait(0)
    finally:
        release.set()
    assert worker.wait(2)
    assert worker.join(2)


def test_callback_failure_is_visible_in_worker_status():
    def fail(result):
        raise RuntimeError("injected callback failure")

    worker = BackgroundIndexer(lambda bg: dict(status="indexed"), on_done=fail)
    worker.start()
    assert worker.join(2)
    assert worker.status()["state"] == "error"
    assert worker.status()["error"] == "injected callback failure"
    assert worker.status()["operation_id"]


def test_disable_waits_for_cancelled_writer_and_releases_lease(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    service = AutoIndexService(tmp_path / "index")
    service.enable(str(project))
    entered, exited = threading.Event(), threading.Event()
    lease_path = service.index_root / "index.build.lock"

    def work(worker):
        lock = BuildLock(lease_path)
        assert lock.try_acquire()
        try:
            entered.set()
            assert worker.cancelled.wait(2)
            worker.check_cancelled()
        finally:
            lock.release()
            exited.set()

    worker = BackgroundIndexer(work)
    service.background = worker
    worker.start()
    try:
        assert entered.wait(2)
        result = service.disable()
        assert not result["enabled"]
        assert exited.is_set()
        assert worker.join(0)
        assert BuildLock(lease_path).state_info() is None
    finally:
        service.disable()
