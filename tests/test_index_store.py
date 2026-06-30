import sqlite3
from pathlib import Path

import pytest

from auto_index_mcp.core.service import AutoIndexService
from auto_index_mcp.indexing.store import IndexStore


def test_index_store_configures_cross_process_sqlite_waits(tmp_path: Path) -> None:
    store = IndexStore(tmp_path / "index.db")

    with store.connect() as conn:
        busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        foreign_keys = conn.execute("PRAGMA foreign_keys").fetchone()[0]

    assert busy_timeout == 30000
    assert foreign_keys == 1


def test_replace_all_keeps_existing_index_on_transient_error(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
    service = AutoIndexService(index_root=tmp_path / "index")
    service.enable(str(project), rebuild=True)
    assert [item["path"] for item in service.all_files()] == ["main.py"]

    def boom(conn, records):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr("auto_index_mcp.indexing.store.insert_many", boom)
    with pytest.raises(sqlite3.OperationalError):
        service.store.replace_all(str(project.resolve()), [], [])

    # A transient (non-corruption) write failure must roll back and keep the
    # existing index file and rows intact - never unlink the database.
    assert service.store.db_path.exists()
    assert [item["path"] for item in service.store.all_files()] == ["main.py"]


def test_replace_all_recovers_from_genuine_corruption(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "main.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
    service = AutoIndexService(index_root=tmp_path / "index")
    service.enable(str(project), rebuild=True)

    store = service.store
    real_write = store._write_all
    calls = {"count": 0}

    def flaky_write(root, records, child_indexes, extra_metadata):
        calls["count"] += 1
        if calls["count"] == 1:
            raise sqlite3.DatabaseError("database disk image is malformed")
        return real_write(root, records, child_indexes, extra_metadata)

    monkeypatch.setattr(store, "_write_all", flaky_write)
    store.replace_all(str(project.resolve()), [], [])

    # Verified corruption: unlink + reinit + retry the write, which then succeeds.
    assert calls["count"] == 2
    assert store.db_path.exists()
    assert store.all_files() == []
