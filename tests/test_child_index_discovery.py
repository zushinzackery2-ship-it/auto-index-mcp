from pathlib import Path

from auto_index_mcp.indexing import locator
from auto_index_mcp.indexing.snapshot import update_watch_snapshot, take_watch_snapshot
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


def test_child_index_discovery_prunes_child_source_tree(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "project"
    child = project / "child"
    deep_source = child / "deep" / "nested"
    deep_source.mkdir(parents=True)

    own_db = _write_empty_index(project)
    _write_empty_index(child)
    visited: list[Path] = []
    real_walk = locator.os.walk

    def spy_walk(root: Path, topdown: bool = True, onerror=None, followlinks: bool = False):
        for item in real_walk(root, topdown=topdown, onerror=onerror, followlinks=followlinks):
            visited.append(Path(item[0]))
            yield item

    monkeypatch.setattr(locator.os, "walk", spy_walk)

    children = discover_child_indexes(project, own_db)

    assert [child_index.path for child_index in children] == ["child"]
    assert deep_source not in visited


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


def test_watch_snapshot_fingerprints_known_child_without_scanning_inside_it(tmp_path: Path) -> None:
    project = tmp_path / "project"
    child = project / "child"
    grandchild = child / "grandchild"
    grandchild.mkdir(parents=True)

    own_db = _write_empty_index(project)
    _write_empty_index(child)
    _write_empty_index(grandchild)

    snapshot = take_watch_snapshot(project, [child], own_db)

    assert sorted(snapshot.child_indexes) == ["child/.auto-index-mcp/index.db"]


def test_update_snapshot_detects_moved_in_child_index_directory(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    own_db = _write_empty_index(project)
    previous = take_watch_snapshot(project, own_db_path=own_db)
    child = project / "child"
    child.mkdir(parents=True)
    _write_empty_index(child)

    assert "child/.auto-index-mcp/index.db" not in previous.child_indexes

    current = update_watch_snapshot(project, previous, {child}, own_db_path=own_db)

    assert sorted(current.child_indexes) == ["child/.auto-index-mcp/index.db"]
    assert not any(path.startswith("child/") for path in current.files)


def test_update_snapshot_ignores_own_index_database_events(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    own_db = _write_empty_index(project)
    previous = take_watch_snapshot(project, own_db_path=own_db)

    current = update_watch_snapshot(project, previous, {own_db, Path(f"{own_db}-wal")}, own_db_path=own_db)

    assert current == previous


def _write_empty_index(root: Path) -> Path:
    db_path = root / ".auto-index-mcp" / "index.db"
    store = IndexStore(db_path)
    store.initialize()
    store.replace_all(str(root.resolve()), [], [])
    return db_path
