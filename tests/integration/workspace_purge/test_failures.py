"""Faults before deletion preserve caches; faults after deletion remain resumable."""
import json
import multiprocessing
import os
from pathlib import Path
import time

import pytest

from auto_index_mcp.registry.markers import write_index_markers
from auto_index_mcp.registry.store import IndexRegistry
from auto_index_mcp.runtime.locking.session import SessionLease
from auto_index_mcp.runtime.files.gate import publication_gate


def test_legacy_mount_blocks_every_target_before_deletion(tmp_path, write_file, make_service):
    write_file(tmp_path / "main.py", "def item():\n    return 1\n")
    service = make_service(tmp_path)
    child = tmp_path / "child"
    child.mkdir()
    cache = child / ".auto-index-mcp"
    write_index_markers(cache, child)
    legacy = SessionLease(cache)
    legacy.acquire()
    original = service.store.db_path.read_bytes()
    started = time.monotonic()
    try:
        result = service.purge(timeout_seconds=1)
        assert result["status"] == "busy", result
        assert result["deleted_bytes"] == result["removed_indexes"] == 0
        assert time.monotonic() - started < 2
        assert (service.index_root / "index.db").read_bytes() == original
        assert (cache / "marker.json").is_file()
        assert "sessions.lock" in str(result["blockers"])
    finally:
        legacy.release()
    resumed = service.purge()
    assert resumed["complete"], resumed
    assert resumed["operation_id"] == result["operation_id"]
    assert resumed["removed_indexes"] == 2


@pytest.mark.parametrize("contents", ["{broken", '{"version":1,"generation":1,"scopes":{"x":{}}}'])
def test_corrupt_control_refuses_without_stopping(tmp_path, write_file, make_service, contents):
    write_file(tmp_path / "main.py", "def item():\n    return 1\n")
    service = make_service(tmp_path)
    path = service.project.control.store.path
    path.write_text(contents, encoding="utf-8")
    result = service.purge()
    assert result["status"] == "refused" and result["error"] == "control-unavailable", result
    assert service.enabled and service.project.session_lease.held
    assert (service.index_root / "index.db").is_file()
    path.unlink()


def test_incomplete_scan_refuses_without_stopping(tmp_path, write_file, make_service, monkeypatch):
    write_file(tmp_path / "main.py", "def item():\n    return 1\n")
    service = make_service(tmp_path)

    def denied(*args, **kwargs):
        raise PermissionError("unreadable child directory")

    monkeypatch.setattr("auto_index_mcp.application.purge.discovery.cache_directories", denied)
    result = service.purge()
    assert result["status"] == "refused" and "unreadable" in result["message"]
    assert service.enabled and service.project.session_lease.held
    assert not service.project.control.store.path.exists()


def test_file_deletion_failure_keeps_attribution_and_resumes(tmp_path, write_file, make_service, monkeypatch):
    write_file(tmp_path / "main.py", "def item():\n    return 1\n")
    service = make_service(tmp_path)
    database = service.store.db_path
    original = Path.unlink

    def denied(path, *args, **kwargs):
        if path == database:
            raise PermissionError("locked database")
        return original(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", denied)
        result = service.purge()
    assert result["status"] == "partial" and not result["complete"], result
    assert result["results"][0]["status"] == "failed"
    assert database.exists() and (service.index_root / "marker.json").exists()
    assert service.registry.get(service.index_root)["state"] == "failed"
    resumed = service.purge()
    assert resumed["complete"] and resumed["operation_id"] == result["operation_id"], resumed


def test_registry_failure_after_delete_is_resumable(tmp_path, write_file, make_service, monkeypatch):
    write_file(tmp_path / "main.py", "def item():\n    return 1\n")
    service = make_service(tmp_path)
    with monkeypatch.context() as patch:
        patch.setattr(IndexRegistry, "unregister", lambda *args, **kwargs: False)
        result = service.purge()
    assert result["status"] == "partial" and result["deleted_bytes"] > 0, result
    assert service.registry.get(service.index_root) is not None
    resumed = service.purge()
    assert resumed["complete"] and resumed["operation_id"] == result["operation_id"], resumed
    assert service.registry.get(service.index_root) is None
    assert not service.index_root.exists()


def test_foreign_marker_refuses_entire_scope(tmp_path, write_file, make_service):
    write_file(tmp_path / "main.py", "def item():\n    return 1\n")
    service = make_service(tmp_path)
    cache = tmp_path / "foreign" / ".auto-index-mcp"
    cache.mkdir(parents=True)
    (cache / "marker.json").write_text(json.dumps(dict(tool="foreign", root=str(cache.parent))), encoding="utf-8")
    result = service.purge()
    assert result["status"] == "refused" and result["deleted_bytes"] == 0, result
    assert service.enabled and service.store.db_path.exists()


def _hold_metadata(path, ready, release):
    with publication_gate(path):
        ready.set()
        assert release.wait(10)


@pytest.mark.skipif(os.name != "nt", reason="Windows metadata namespace gate")
def test_control_metadata_contention_obeys_whole_call_deadline(tmp_path, write_file, make_service):
    write_file(tmp_path / "main.py", "def item():\n    return 1\n")
    service = make_service(tmp_path)
    context = multiprocessing.get_context("spawn")
    ready, release = context.Event(), context.Event()
    worker = context.Process(target=_hold_metadata, args=(service.project.control.store.path, ready, release))
    worker.start()
    try:
        assert ready.wait(5)
        started = time.monotonic()
        result = service.purge(timeout_seconds=1)
        assert time.monotonic() - started < 2, result
        assert result["status"] == "refused" and result["deleted_bytes"] == 0, result
        assert service.enabled and service.store.db_path.exists()
    finally:
        release.set()
        worker.join(5)
        if worker.is_alive():
            worker.terminate()
            worker.join(5)
    assert worker.exitcode == 0
    assert service.purge()["complete"]
