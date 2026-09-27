"""Multi-process enable(rebuild=False) contention regressions.

Locks down the kill-churn livelock: an MCP server process killed mid-build
leaves index.build.lock behind, and before the dead-owner reclaim every other
process was falsely told "indexing-in-other-process" for the full 120s stale
window while nobody built anything. Also covers the cross-process embeddings
lock and the same-root re-enable fast path that removes per-poll store
re-initialization churn.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from auto_index_mcp.core.service import AutoIndexService
from auto_index_mcp.embedding.backend import BagHashEmbedder
from auto_index_mcp.runtime.leases import BuildLock

CODE_PY = "def compute(rows):\n    return sum(rows)\n"


def _dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait(timeout=30)
    return proc.pid


def _plant_lock(path: Path, token_head: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{token_head}:0:0\n0", encoding="ascii")


def test_dead_owner_lock_reclaimed_immediately(tmp_path: Path) -> None:
    lock_path = tmp_path / "index.build.lock"
    _plant_lock(lock_path, str(_dead_pid()))

    lock = BuildLock(lock_path)
    assert lock.try_acquire() is True
    lock.release()


def test_live_owner_lock_not_reclaimed(tmp_path: Path) -> None:
    lock_path = tmp_path / "index.build.lock"
    holder = BuildLock(lock_path)
    assert holder.try_acquire()
    lock = BuildLock(lock_path)
    try:
        assert lock.try_acquire() is False
        assert lock_path.exists()
    finally:
        holder.release()


def test_unparseable_metadata_does_not_block_kernel_lock(tmp_path: Path) -> None:
    lock_path = tmp_path / "index.build.lock"
    _plant_lock(lock_path, "garbage-token")

    fresh = BuildLock(lock_path)
    try:
        assert fresh.try_acquire() is True
    finally:
        fresh.release()


def test_state_info_reports_holder_liveness(tmp_path: Path) -> None:
    lock_path = tmp_path / "index.build.lock"
    holder = BuildLock(lock_path)
    assert holder.try_acquire()
    try:
        info = BuildLock(lock_path).state_info()
        assert info is not None
        assert info["holder_pid"] == os.getpid()
        assert info["holder_alive"] is True
        assert info["lock_age_seconds"] >= 0
    finally:
        holder.release()


def test_enable_reclaims_dead_owner_lock_and_builds(tmp_path: Path, write_file) -> None:
    project = tmp_path / "proj"
    write_file(project / "code.py", CODE_PY)
    index_root = tmp_path / ".idx"
    _plant_lock(index_root / "index.build.lock", str(_dead_pid()))

    service = AutoIndexService(index_root=index_root)
    try:
        result = service.enable_reusing_index(str(project), rebuild=False, wait_seconds=15.0)
        assert result["status"] == "indexed"
        assert result["file_count"] == 1
    finally:
        service.disable()


def test_enable_reports_holder_diagnostics_when_lock_held(tmp_path: Path, write_file) -> None:
    project = tmp_path / "proj"
    write_file(project / "code.py", CODE_PY)
    index_root = tmp_path / ".idx"
    index_root.mkdir()
    holder = BuildLock(index_root / "index.build.lock")
    assert holder.try_acquire() is True

    service = AutoIndexService(index_root=index_root)
    try:
        result = service.enable_reusing_index(str(project), rebuild=False, wait_seconds=5.0)
        assert result["status"] == "indexing-in-background"
        assert result["index_build"]["phase"] == "waiting-for-writer"
        assert result["build_lock"]["holder_pid"] == os.getpid()
        assert result["build_lock"]["holder_alive"] is True
    finally:
        holder.release()
        service.disable()


def test_reenable_same_root_reuses_stores(tmp_path: Path, write_file, make_service) -> None:
    project = tmp_path / "proj"
    write_file(project / "code.py", CODE_PY)
    service = make_service(project)
    store_before = service.store
    embedding_store_before = service.embedding_store

    result = service.enable_reusing_index(str(project), rebuild=False, wait_seconds=10.0)

    assert service.store is store_before
    assert service.embedding_store is embedding_store_before
    assert result["enabled"] is True
    assert result["file_count"] == 1


def test_reenable_same_root_still_dispatches_rebuild_when_stale(
    tmp_path: Path, write_file, make_service
) -> None:
    project = tmp_path / "proj"
    write_file(project / "code.py", CODE_PY)
    service = make_service(project)
    store_before = service.store
    service.store.update_metadata({"version": 0})

    result = service.enable_reusing_index(str(project), rebuild=False, wait_seconds=15.0)

    assert service.store is store_before
    assert result["status"] == "indexed"
    assert service.store.get_metadata_map()["version"] != 0


def test_full_embedding_skipped_while_other_process_holds_lock(
    tmp_path: Path, write_file, install_embedder, make_service, wait_embedding, monkeypatch
) -> None:
    install_embedder(BagHashEmbedder(dim=32))
    index_root = tmp_path / ".idx"
    index_root.mkdir()
    holder = BuildLock(index_root / "embeddings.build.lock")
    assert holder.try_acquire() is True

    project = tmp_path / "proj"
    write_file(project / "code.py", CODE_PY)
    service = make_service(project)
    result = wait_embedding(service)
    assert result["status"] == "embedding-in-other-process"
    assert result["build_lock"]["holder_pid"] == os.getpid()

    holder.release()
    service.ensure_embedding_background()
    result = wait_embedding(service)
    assert result["status"] == "embedded"
    assert service.embedding_indexer is not None
    assert service.embedding_indexer.count() > 0


def test_rebuild_embedding_retries_after_lock_release(
    tmp_path: Path, write_file, install_embedder, make_service, wait_embedding, monkeypatch
) -> None:
    install_embedder(BagHashEmbedder(dim=32))
    index_root = tmp_path / ".idx"
    index_root.mkdir()
    holder = BuildLock(index_root / "embeddings.build.lock")
    assert holder.try_acquire() is True

    project = tmp_path / "proj"
    write_file(project / "code.py", CODE_PY)
    service = make_service(project)
    result = wait_embedding(service)
    assert result["status"] == "embedding-in-other-process"
    holder.release()
    service.ensure_embedding_background()
    result = wait_embedding(service)
    assert result.get("embedded", 0) > 0
