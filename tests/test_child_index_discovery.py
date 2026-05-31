from pathlib import Path

from auto_index_mcp.indexing.snapshot import take_watch_snapshot
from auto_index_mcp.indexing.store import IndexStore
from auto_index_mcp.workspace.discovery import discover_child_indexes


def test_child_index_discovery_prunes_excluded_directories(tmp_path: Path) -> None:
    project = tmp_path / "project"
    included_child = project / "src" / "child"
    excluded_child = project / "node_modules" / "pkg"
    included_child.mkdir(parents=True)
    excluded_child.mkdir(parents=True)

    own_db = _write_empty_index(project)
    _write_empty_index(included_child)
    _write_empty_index(excluded_child)

    children = discover_child_indexes(project, own_db)

    assert [child.path for child in children] == ["src/child"]


def test_watch_snapshot_child_indexes_prune_excluded_directories(tmp_path: Path) -> None:
    project = tmp_path / "project"
    included_child = project / "src" / "child"
    excluded_child = project / "node_modules" / "pkg"
    included_child.mkdir(parents=True)
    excluded_child.mkdir(parents=True)

    own_db = _write_empty_index(project)
    _write_empty_index(included_child)
    _write_empty_index(excluded_child)

    snapshot = take_watch_snapshot(project, own_db_path=own_db)

    assert sorted(snapshot.child_indexes) == ["src/child/.auto-index-mcp/index.db"]


def _write_empty_index(root: Path) -> Path:
    db_path = root / ".auto-index-mcp" / "index.db"
    store = IndexStore(db_path)
    store.initialize()
    store.replace_all(str(root.resolve()), [], [])
    return db_path