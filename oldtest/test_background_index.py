import threading
import time
from pathlib import Path

from auto_index_mcp.core.background_indexer import (
    BackgroundIndexer,
    PHASE_SCANNING,
    STATE_DONE,
    STATE_ERROR,
)
from auto_index_mcp.core.service import AutoIndexService
from auto_index_mcp.mcp_api.lifecycle import start_or_defer_auto_watch


def test_background_indexer_runs_work_and_reports_done() -> None:
    payload = {"status": "indexed", "file_count": 3}

    def work(indexer: BackgroundIndexer) -> dict:
        assert indexer.is_running()
        return payload

    bg = BackgroundIndexer(work)
    bg.start()
    assert bg.wait(5.0)
    snap = bg.status()
    assert snap["state"] == STATE_DONE
    assert snap["last_result"] == payload
    assert snap["elapsed_seconds"] is not None


def test_background_indexer_captures_error() -> None:
    def work(indexer: BackgroundIndexer) -> dict:
        raise RuntimeError("boom")

    bg = BackgroundIndexer(work)
    bg.start()
    assert bg.wait(5.0)
    snap = bg.status()
    assert snap["state"] == STATE_ERROR
    assert "boom" in (snap["error"] or "")
    assert snap["last_result"] is None


def test_background_indexer_start_is_idempotent() -> None:
    gate = threading.Event()
    calls = {"n": 0}

    def work(indexer: BackgroundIndexer) -> dict:
        calls["n"] += 1
        gate.wait(5.0)
        return {"ok": True}

    bg = BackgroundIndexer(work)
    bg.start()
    bg.start()  # already running -> ignored
    assert bg.is_running()
    gate.set()
    assert bg.wait(5.0)
    assert calls["n"] == 1


def test_background_indexer_set_phase_visible() -> None:
    gate = threading.Event()
    seen: dict[str, str] = {}

    def work(indexer: BackgroundIndexer) -> dict:
        indexer.set_phase(PHASE_SCANNING)
        seen["phase"] = indexer.status()["phase"]
        gate.wait(5.0)
        return {"ok": True}

    bg = BackgroundIndexer(work)
    bg.start()
    for _ in range(100):
        if "phase" in seen:
            break
        time.sleep(0.02)
    gate.set()
    assert bg.wait(5.0)
    assert seen["phase"] == PHASE_SCANNING


def test_background_indexer_on_done_called_on_success() -> None:
    done: dict[str, object] = {"called": False, "result": None}

    def work(indexer: BackgroundIndexer) -> dict:
        return {"status": "indexed"}

    def on_done(result: dict) -> None:
        done["called"] = True
        done["result"] = result

    bg = BackgroundIndexer(work, on_done=on_done)
    bg.start()
    assert bg.wait(5.0)
    for _ in range(100):
        if done["called"]:
            break
        time.sleep(0.02)
    assert done["called"] is True
    assert done["result"] == {"status": "indexed"}


def test_background_indexer_on_done_skipped_on_error() -> None:
    done = {"called": False}

    def work(indexer: BackgroundIndexer) -> dict:
        raise RuntimeError("fail")

    def on_done(result: dict) -> None:
        done["called"] = True

    bg = BackgroundIndexer(work, on_done=on_done)
    bg.start()
    assert bg.wait(5.0)
    time.sleep(0.15)
    assert done["called"] is False


def test_service_first_build_auto_starts_watcher(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("def a():\n    return 1\n", encoding="utf-8")

    service = AutoIndexService(index_root=tmp_path / "index")
    gate = threading.Event()
    real = service._rebuild_now

    def blocked(indexer=None, context=None):
        gate.wait(5.0)
        return real(indexer, context)

    service._rebuild_now = blocked
    result = service.enable_reusing_index(str(project))
    assert result["status"] == "indexing-in-background"
    service.request_auto_watch_after_build()
    gate.set()

    assert service.background is not None
    assert service.background.wait(10.0)
    try:
        for _ in range(100):
            if service.watcher is not None and service.watcher.is_running():
                break
            time.sleep(0.05)
        assert service.watcher is not None and service.watcher.is_running()
        assert service._auto_watch_after_build is False
    finally:
        service.stop_watcher()


def test_service_disable_cancels_deferred_auto_watch(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("def a():\n    return 1\n", encoding="utf-8")

    service = AutoIndexService(index_root=tmp_path / "index")
    gate = threading.Event()
    real = service._rebuild_now

    def blocked(indexer=None, context=None):
        gate.wait(5.0)
        return real(indexer, context)

    monkeypatch.setattr(service, "_rebuild_now", blocked)
    result = service.enable_reusing_index(str(project))
    assert result["status"] == "indexing-in-background"
    service.request_auto_watch_after_build()

    disabled = service.disable()
    assert disabled["enabled"] is False
    assert service._auto_watch_after_build is False

    gate.set()
    assert service.background is not None
    assert service.background.wait(10.0)
    time.sleep(0.1)
    assert service.watcher_status() == {"running": False}


def test_service_status_exposes_background_index(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("def a():\n    return 1\n", encoding="utf-8")

    service = AutoIndexService(index_root=tmp_path / "index")
    service.enable_reusing_index(str(project))
    assert service.background is not None
    assert service.background.wait(10.0)

    status = service.status()
    assert "background_index" in status
    assert status["background_index"]["state"] == STATE_DONE


def test_status_build_timers_track_background_index(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("def a():\n    return 1\n", encoding="utf-8")

    service = AutoIndexService(index_root=tmp_path / "index")
    service.enable_reusing_index(str(project))
    assert service.background is not None
    assert service.background.wait(10.0)

    timers = service.status()["build_timers"]
    index_timer = timers["index"]
    assert index_timer["state"] == STATE_DONE
    assert index_timer["running"] is False
    assert index_timer["elapsed_seconds"] is not None
    # Embedding never ran in this minimal env: timer stays idle, not absent.
    assert timers["embedding"]["running"] is False


def test_build_timer_elapsed_ticks_while_running() -> None:
    release = threading.Event()

    def work(indexer: BackgroundIndexer) -> dict:
        release.wait(5.0)
        return {"status": "indexed"}

    bg = BackgroundIndexer(work)
    bg.start()
    try:
        first = bg.timer()
        assert first["running"] is True
        assert first["elapsed_seconds"] is not None
        time.sleep(0.05)
        second = bg.timer()
        assert second["elapsed_seconds"] >= first["elapsed_seconds"]
    finally:
        release.set()
    assert bg.wait(5.0)
    done = bg.timer()
    assert done["running"] is False
    assert done["finished_at"] is not None


def test_build_timers_record_synchronous_rebuild(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("def a():\n    return 1\n", encoding="utf-8")

    service = AutoIndexService(index_root=tmp_path / "index")
    # enable(rebuild=True) takes the synchronous path: no BackgroundIndexer handle.
    service.enable(str(project), rebuild=True)
    assert service.background is None

    index_timer = service.status()["build_timers"]["index"]
    assert index_timer["state"] == STATE_DONE
    assert index_timer["elapsed_seconds"] is not None
    assert index_timer["started_at"] is not None
    assert index_timer["finished_at"] is not None


def test_auto_watch_race_guard_uses_successful_background_result() -> None:
    service = _AutoWatchService()
    background = BackgroundIndexer(lambda indexer: {"status": "indexed", "updated_at": 1.0})
    background.start()
    assert background.wait(5.0)
    service.background = background

    result = start_or_defer_auto_watch(service, {"status": "indexing-in-background"})

    assert result["watcher"] == {"running": True}
    assert service.started == 1
    assert service.deferred is False


def test_auto_watch_race_guard_does_not_start_after_background_error() -> None:
    service = _AutoWatchService()

    def fail(indexer: BackgroundIndexer) -> dict:
        raise RuntimeError("build failed")

    background = BackgroundIndexer(fail)
    background.start()
    assert background.wait(5.0)
    service.background = background

    result = start_or_defer_auto_watch(service, {"status": "indexing-in-background"})

    assert "watcher" not in result
    assert service.started == 0
    assert service.deferred is False


class _AutoWatchService:
    def __init__(self) -> None:
        self.background: BackgroundIndexer | None = None
        self.started = 0
        self.deferred = False

    def can_start_auto_watch(self, result: dict | None) -> bool:
        return result is not None and result.get("updated_at") == 1.0

    def start_watcher(self, wait_ready: bool = False) -> dict:
        _ = wait_ready
        self.started += 1
        return {"running": True}

    def request_auto_watch_after_build(self) -> None:
        self.deferred = True

    def cancel_auto_watch_after_build(self) -> None:
        self.deferred = False
