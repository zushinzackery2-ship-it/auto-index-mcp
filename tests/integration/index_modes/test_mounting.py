"""Opt-in mounting stays isolated from the ordinary workspace index."""
import time

from auto_index_mcp.core.service import AutoIndexService
from auto_index_mcp.application.watch_session import WatchSession


def _project(tmp_path, write_file):
    root = tmp_path / "project"
    child = root / "library"
    write_file(root / "main.py", "def main():\n    return 1\n")
    write_file(child / "child.py", "def child_value():\n    return 2\n")
    return root, child


def _wait(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("watcher did not publish the expected mode-specific state")


def test_default_and_mounted_instances_keep_independent_indexes(tmp_path, write_file):
    root, child = _project(tmp_path, write_file)
    nested, flat, mounted = AutoIndexService(), AutoIndexService(), AutoIndexService()
    try:
        nested.enable(str(child), refresh_embedder=False)
        flat.enable(str(root), refresh_embedder=False)
        mounted.enable(str(root), refresh_embedder=False, mount_child_indexes=True)
        assert flat.store.db_path != mounted.store.db_path
        assert flat.status()["file_count"] == 2
        assert mounted.status()["file_count"] == 1
        assert flat.status()["child_index_count"] == 0
        assert mounted.status()["child_index_count"] == 1
        for service in (flat, mounted):
            assert service.symbol_body("child_value")["path"] == "library/child.py"
            assert service.status()["total_file_count"] == 2
        mounted.rebuild_sync()
        assert flat.store.child_indexes() == []
        assert flat.can_reuse_index_for(root)
        assert mounted.can_reuse_index_for(root)
    finally:
        mounted.disable()
        flat.disable()
        nested.disable()


def test_omitted_enable_parameter_returns_to_default_mode(tmp_path, write_file):
    root, child = _project(tmp_path, write_file)
    nested, parent = AutoIndexService(), AutoIndexService()
    try:
        nested.enable(str(child), refresh_embedder=False)
        parent.enable(str(root), refresh_embedder=False, mount_child_indexes=True)
        mounted_path = parent.store.db_path
        parent.enable(str(root), refresh_embedder=False)
        assert parent.status()["mount_child_indexes"] is False
        assert parent.store.db_path != mounted_path
        assert parent.status()["file_count"] == 2
        assert parent.status()["child_index_count"] == 0
        parent.enable_reusing_index(str(root), mount_child_indexes=True)
        assert parent.store.db_path == mounted_path
        assert parent.status()["child_index_count"] == 1
    finally:
        parent.disable()
        nested.disable()


def test_old_cache_policy_cannot_implicitly_enable_mounting(tmp_path, write_file):
    root, child = _project(tmp_path, write_file)
    initial, resumed = AutoIndexService(), AutoIndexService()
    try:
        initial.enable(str(root), refresh_embedder=False)
        initial.store.update_metadata(dict(mount_child_indexes=True))
        initial.disable()
        resumed.enable_reusing_index(str(root), wait_seconds=10)
        assert resumed.store.get_metadata_map()["mount_child_indexes"] is False
        assert resumed.store.child_indexes() == []
        assert resumed.status()["file_count"] == 2
        assert resumed.symbol_body("child_value")["path"] == "library/child.py"
    finally:
        resumed.disable()
        initial.disable()


def test_explicit_mounting_never_creates_indexes_for_source_subdirectories(tmp_path, write_file):
    root, child = _project(tmp_path, write_file)
    write_file(child / "deep" / "deep.py", "def deep_value():\n    return 3\n")
    parent = AutoIndexService()
    try:
        result = parent.enable(str(root), refresh_embedder=False, mount_child_indexes=True)
        assert result["child_index_count"] == 0
        assert result["file_count"] == 3
        assert not list(child.rglob(".auto-index-mcp"))
        assert not list(child.rglob(".auto-index-mcp-mounted"))
    finally:
        parent.disable()


def test_external_child_publication_is_discovered_without_source_events(tmp_path, write_file, monkeypatch):
    monkeypatch.setenv("AUTO_INDEX_CACHE_MODE", "user")
    root, child = _project(tmp_path, write_file)
    parent, nested = AutoIndexService(), AutoIndexService()
    try:
        parent.enable(str(root), refresh_embedder=False, mount_child_indexes=True)
        parent.start_watcher(debounce_seconds=0.05, wait_ready=True)
        assert parent.status()["child_index_count"] == 0
        nested.enable(str(child), refresh_embedder=False)
        _wait(lambda: parent.status()["child_index_count"] == 1)
        assert parent.status()["file_count"] == 1
        assert parent.status()["total_file_count"] == 2
        assert parent.symbol_body("child_value")["path"] == "library/child.py"
        assert not list(root.rglob(".auto-index-mcp"))
    finally:
        parent.disable()
        nested.disable()


def test_default_watcher_keeps_own_child_source_after_child_publication(tmp_path, write_file):
    root, child = _project(tmp_path, write_file)
    parent, nested = AutoIndexService(), AutoIndexService()
    try:
        parent.enable(str(root), refresh_embedder=False)
        parent.start_watcher(debounce_seconds=0.05, wait_ready=True)
        nested.enable(str(child), refresh_embedder=False)
        write_file(child / "child.py", "def changed_child():\n    return 3\n")
        _wait(lambda: bool(parent.symbol_search("changed_child")["items"]))
        assert parent.status()["mount_child_indexes"] is False
        assert parent.status()["child_index_count"] == 0
        assert parent.status()["file_count"] == 2
    finally:
        parent.disable()
        nested.disable()


def test_mounted_maintenance_does_not_walk_source_tree(tmp_path, write_file, monkeypatch):
    root, child = _project(tmp_path, write_file)
    parent, nested = AutoIndexService(), AutoIndexService()
    try:
        nested.enable(str(child), refresh_embedder=False)
        parent.enable(str(root), refresh_embedder=False, mount_child_indexes=True)
        session = WatchSession(root, parent.store, parent.rebuild_sync, mount_child_indexes=True)
        expected = session.baseline().child_indexes

        def forbidden(*args, **kwargs):
            raise AssertionError("periodic child maintenance walked the source tree")

        monkeypatch.setattr("auto_index_mcp.workspace.discovery.iter_index_databases", forbidden)
        assert session.children_snapshot() == expected
    finally:
        parent.disable()
        nested.disable()


def test_mounted_project_cache_is_reused_after_environment_mode_is_unset(tmp_path, write_file, monkeypatch):
    root, child = _project(tmp_path, write_file)
    first, resumed = AutoIndexService(), AutoIndexService()
    try:
        first.enable(str(root), refresh_embedder=False, mount_child_indexes=True)
        expected = first.store.db_path
        first.disable()
        monkeypatch.delenv("AUTO_INDEX_CACHE_MODE", raising=False)
        resumed.enable_reusing_index(str(root), mount_child_indexes=True, wait_seconds=10)
        assert resumed.store.db_path == expected
        assert resumed.status()["total_file_count"] == 2
    finally:
        resumed.disable()
        first.disable()
