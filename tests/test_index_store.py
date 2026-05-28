from pathlib import Path

from auto_index_mcp.indexing.store import IndexStore


def test_index_store_configures_cross_process_sqlite_waits(tmp_path: Path) -> None:
    store = IndexStore(tmp_path / "index.db")

    with store.connect() as conn:
        busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        foreign_keys = conn.execute("PRAGMA foreign_keys").fetchone()[0]

    assert busy_timeout == 30000
    assert foreign_keys == 1
