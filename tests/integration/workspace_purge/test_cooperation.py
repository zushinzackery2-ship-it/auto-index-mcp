"""Accepted requests drain, unrelated parents continue, and resumes are per instance."""
from concurrent.futures import ThreadPoolExecutor
import threading
import time

import pytest

from auto_index_mcp.core.service import AutoIndexService
from auto_index_mcp.domain.errors import ServiceError
from auto_index_mcp.runtime.background import BackgroundIndexer
from auto_index_mcp.runtime.control.contracts import ActivationIntent


def test_explicit_resume_only_reactivates_calling_instance(tmp_path, write_file, make_service):
    write_file(tmp_path / "main.py", "def item():\n    return 1\n")
    first, second = make_service(tmp_path), make_service(tmp_path)
    result = first.purge()
    assert result["complete"] and result["stopped_instances"] == 2, result
    first.enable(str(tmp_path), refresh_embedder=False)
    assert first.symbol_search("item")["items"]
    with pytest.raises(ServiceError, match="suspended"):
        second.symbol_search("item")
    restarted = AutoIndexService()
    with pytest.raises(ServiceError) as error:
        restarted.enable(str(tmp_path), activation=ActivationIntent.AUTOMATIC)
    assert error.value.code == "workspace-suspended"
    restarted.root_path = tmp_path
    assert restarted.status()["last_purge"]["operation_id"] == result["operation_id"]
    assert not second.enabled
    newer = first.purge()
    assert newer["complete"] and newer["operation_id"] != result["operation_id"], newer


def test_outer_mount_stops_but_its_cache_survives(tmp_path, write_file, make_service):
    child = tmp_path / "project"
    write_file(child / "main.py", "def item():\n    return 1\n")
    write_file(tmp_path / "outer.py", "def outer():\n    return 2\n")
    inner, outer = make_service(child), make_service(tmp_path)
    assert len(outer.all_files()) == 2
    outside_cache = outer.store.db_path
    result = inner.purge()
    assert result["complete"] and result["stopped_instances"] == 2, result
    assert result["removed_indexes"] == 1 and outside_cache.exists()
    assert not outer.enabled and outer.store is None
    assert any(row["role"] == "external-mounter" for row in result["participants"])


def test_parent_without_mount_keeps_running(tmp_path, write_file, make_service):
    write_file(tmp_path / "outer.py", "def outer():\n    return 2\n")
    outer = make_service(tmp_path)
    child = tmp_path / "project"
    write_file(child / "main.py", "def item():\n    return 1\n")
    inner = make_service(child)
    assert inner.purge()["complete"]
    time.sleep(0.3)
    assert outer.enabled and outer.project.session_lease.held
    assert outer.symbol_search("outer")["items"]


def test_long_request_timeout_preserves_cache_then_retry_drains(tmp_path, write_file, make_service):
    write_file(tmp_path / "main.py", "def item():\n    return 1\n")
    service = make_service(tmp_path)
    accepted, release = threading.Event(), threading.Event()

    def query():
        with service._request_lock:
            accepted.set()
            assert release.wait(5)

    with ThreadPoolExecutor() as pool:
        request = pool.submit(query)
        assert accepted.wait(2)
        try:
            result = service.purge(timeout_seconds=1)
            assert result["status"] == "stop-timeout" and result["removed_indexes"] == 0, result
            assert (service.index_root / "index.db").exists()
            assert service.status()["suspension"]["phase"] == "stop-timeout"
        finally:
            release.set()
            request.result(timeout=2)
    assert service.purge()["complete"]


def test_delayed_build_and_watch_are_cancelled(tmp_path, write_file, make_service):
    write_file(tmp_path / "main.py", "def item():\n    return 1\n")
    service = make_service(tmp_path)
    ran = threading.Event()
    service.background = BackgroundIndexer(lambda worker: ran.set() or dict(status="indexed"))
    service.background.start(delay_seconds=20)
    service.request_auto_watch_after_build()
    result = service.purge()
    assert result["complete"] and not ran.is_set(), result
    assert service.background.join(0)
    assert service.watcher is None


def test_status_during_suspension_never_reads_live_embedding_view(tmp_path, write_file, make_service, monkeypatch):
    write_file(tmp_path / "main.py", "def item():\n    return 1\n")
    service = make_service(tmp_path)
    store = service.project.control.store
    record, owner = store.begin(tmp_path, time.monotonic() + 5)
    try:
        service.embedding_indexer = object()
        status = service.status()
        assert status["embedding"]["state"] == "suspended"
        assert status["suspension"]["operation_id"] == record["operation_id"]
    finally:
        service.embedding_indexer = None
        store.update(record, phase="interrupted")
        owner.release()
