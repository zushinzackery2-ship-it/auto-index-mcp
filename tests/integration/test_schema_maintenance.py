import pytest

from auto_index_mcp.runtime.leases import BuildLock
from auto_index_mcp.storage.index import IndexStore
from auto_index_mcp.storage.schema import SCHEMA_VERSION


def test_schema_upgrade_refuses_live_watcher_and_resumes_after_release(tmp_path):
    store = IndexStore(tmp_path / "index.db")
    store.initialize()
    with store.connect() as conn:
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION - 1}")
    owner = BuildLock(tmp_path / "watcher.lock")
    assert owner.try_acquire()
    try:
        with pytest.raises(TimeoutError, match="watcher.lock"):
            store.initialize()
        with store.read_connect() as conn:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION - 1
    finally:
        owner.release()
    store.initialize()
    with store.read_connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
