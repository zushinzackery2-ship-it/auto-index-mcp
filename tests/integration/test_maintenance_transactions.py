from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from auto_index_mcp.cli import main
from auto_index_mcp.core.service import AutoIndexService
from auto_index_mcp.storage.embeddings import EmbeddingStore
from auto_index_mcp.runtime.leases import BuildLock
from auto_index_mcp.storage.index import IndexStore
from auto_index_mcp.registry import IndexRegistry, write_index_markers


def test_concurrent_registrations_preserve_every_directory(tmp_path):
    registry = IndexRegistry(tmp_path / "registry")
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(
            lambda i: registry.register(tmp_path / f"p{i}", tmp_path / f"p{i}" / ".auto-index-mcp", "test"),
            range(32),
        ))
    assert all(results)
    assert len(registry.entries()) == 32


def test_one_root_can_own_multiple_indexes(tmp_path):
    registry = IndexRegistry()
    assert registry.register(tmp_path, tmp_path / ".auto-index-mcp", "test")
    assert registry.register(tmp_path, tmp_path / ".smoke-index", "test", ephemeral=True)
    assert len(registry.entries()) == 2
    assert registry.unregister(tmp_path / ".smoke-index")
    assert len(registry.entries()) == 1


def test_cleanup_holds_all_writer_leases(tmp_path):
    source = tmp_path / "code.py"
    source.write_text("def value():\n    return 1\n", encoding="utf-8")
    service = AutoIndexService()
    service.semantic_enabled = False
    try:
        service.enable(str(tmp_path))
        service.start_watcher(wait_ready=True)
        assert main(["clean", str(tmp_path), "-y"]) == 1
        assert service.store.get_file("code.py") is not None
        assert service.registry.is_registered(tmp_path)
    finally:
        service.disable()


def test_failed_deletion_preserves_registry_and_marker(tmp_path, monkeypatch):
    directory = tmp_path / ".auto-index-mcp"
    write_index_markers(directory, tmp_path)
    database = directory / "index.db"
    database.write_bytes(b"fixture")
    registry = IndexRegistry()
    registry.register(tmp_path, directory, "test")
    original = type(database).unlink

    def locked(path, *args, **kwargs):
        if path == database:
            raise PermissionError("injected sharing violation")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(type(database), "unlink", locked)
    assert main(["clean", str(tmp_path), "-y"]) == 1
    assert registry.is_registered(tmp_path)
    assert (directory / "marker.json").exists()


@pytest.mark.parametrize("store_type,name", [(IndexStore, "index.db"), (EmbeddingStore, "embeddings.db")])
def test_initialization_quarantines_corruption(tmp_path, store_type, name):
    path = tmp_path / name
    path.write_bytes(b"not a sqlite database")
    store_type(path).initialize()
    with store_type(path).read_connect() as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert list(tmp_path.glob(f"{name}.corrupt-*"))


def test_corruption_recovery_respects_live_owner(tmp_path):
    path = tmp_path / "index.db"
    path.write_bytes(b"not a sqlite database")
    owner = BuildLock(tmp_path / "watcher.lock")
    assert owner.try_acquire()
    try:
        with pytest.raises(TimeoutError):
            IndexStore(path).initialize()
        assert path.read_bytes() == b"not a sqlite database"
    finally:
        owner.release()
