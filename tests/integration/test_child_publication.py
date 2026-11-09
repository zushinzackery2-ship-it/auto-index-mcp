"""A child is a navigation boundary only after its first atomic publication."""
from auto_index_mcp.application.watch_session import WatchSession
from auto_index_mcp.core.service import AutoIndexService


def service_for(root, rebuild=True, mount_child_indexes=False):
    service = AutoIndexService()
    service.semantic_enabled = False
    service.enable(str(root), rebuild=rebuild, refresh_embedder=False, mount_child_indexes=mount_child_indexes)
    return service


def test_unpublished_child_does_not_hide_its_source_or_create_duplicate_links(tmp_path, write_file):
    root, child = tmp_path / "project", tmp_path / "project" / "child"
    write_file(root / "root.py", "def root_only():\n    pass\n")
    write_file(child / "child.py", "def child_only():\n    pass\n")
    parent = service_for(root, mount_child_indexes=True)
    nested = None
    try:
        session = WatchSession(root, parent.store, parent.rebuild_sync, mount_child_indexes=True)
        before = session.baseline()
        nested = service_for(child, rebuild=False)
        unpublished = session.snapshot()
        session.apply(before, unpublished)
        assert parent.status()["file_count"] == 2
        assert parent.status()["child_index_count"] == 0
        assert unpublished.child_indexes == dict()
        nested.rebuild_sync()
        published = session.snapshot()
        session.apply(unpublished, published)
        assert parent.status()["file_count"] == 1
        assert parent.status()["child_index_count"] == 1
        assert parent.status()["total_file_count"] == 2
        assert [item["path"] for item in parent.store.file_headers()] == ["root.py"]
    finally:
        parent.disable()
        if nested is not None:
            nested.disable()


def test_nested_external_indexes_share_one_snapshot_boundary(tmp_path, write_file, monkeypatch):
    monkeypatch.setenv("AUTO_INDEX_CACHE_MODE", "user")
    root, child = tmp_path / "project", tmp_path / "project" / "child"
    grandchild = child / "nested"
    write_file(root / "root.py", "def root_only():\n    pass\n")
    write_file(child / "child.py", "def child_only():\n    pass\n")
    write_file(grandchild / "nested.py", "def nested_only():\n    pass\n")
    services = []
    try:
        for project in (grandchild, child, root):
            services.append(service_for(project, mount_child_indexes=True))
        parent = services[-1]
        session = WatchSession(root, parent.store, parent.rebuild_sync, mount_child_indexes=True)
        assert session.snapshot().child_indexes == session.baseline().child_indexes
        assert parent.status()["total_file_count"] == 3
        assert parent.symbol_body("nested_only")["path"] == "child/nested/nested.py"
    finally:
        for service in reversed(services):
            service.disable()


def test_watcher_log_scope_follows_data_directory_with_external_locks(tmp_path, write_file):
    write_file(tmp_path / "main.py", "def active():\n    pass\n")
    service = service_for(tmp_path)
    try:
        service.start_watcher(debounce_seconds=0.1, wait_ready=True)
        with open(service.project.log_path, encoding="utf-8") as stream:
            assert "watcher owner root=" in stream.read()
    finally:
        service.disable()
