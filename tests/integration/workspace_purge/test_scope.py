"""Public workspace retirement: complete nesting and cache placement coverage."""
from pathlib import Path

from auto_index_mcp.core.service import AutoIndexService
from auto_index_mcp.registry import IndexRegistry
from auto_index_mcp.registry.markers import write_index_markers
from auto_index_mcp.application.purge import discovery


def test_purge_stops_parent_and_all_nested_indexes(tmp_path, write_file, make_service):
    root = tmp_path / "project"
    child, grandchild = root / "child", root / "child" / "nested"
    for directory in (root, child, grandchild):
        write_file(directory / "code.py", "def item():\n    return 1\n")
    leaf, middle, parent = [make_service(directory) for directory in (grandchild, child, root)]
    parent.start_watcher(wait_ready=True)
    parent.all_files()
    result = parent.purge()
    assert result["complete"], result
    assert result["selected_indexes"] == result["removed_indexes"] == 3
    assert result["stopped_instances"] == 3
    for service in (leaf, middle, parent):
        assert not service.enabled
        assert service.store is None
        assert not (service.index_root / "index.db").exists()
        assert service.status()["suspension"]["phase"] == "purged"
    assert Path(result["report_path"]).is_file()


def test_preview_preserves_running_and_persistent_state(tmp_path, write_file, make_service):
    write_file(tmp_path / "code.py", "def item():\n    return 1\n")
    service = make_service(tmp_path)
    paths = [service.store.db_path, service.index_root / "marker.json", service.registry.path()]
    before = [path.read_bytes() for path in paths]
    preview = service.purge(dry_run=True)
    assert preview["status"] == "preview"
    assert preview["selected_indexes"] == 1
    assert service.enabled and service.project.session_lease.held
    assert before == [path.read_bytes() for path in paths]
    assert not service.project.control.store.path.exists()


def test_disabled_binding_purges_multiple_external_caches(tmp_path, write_file, monkeypatch):
    root = tmp_path / "source"
    write_file(root / "code.py", "def item():\n    return 1\n")
    monkeypatch.setenv("AUTO_INDEX_CACHE_MODE", "user")
    first = AutoIndexService()
    second = AutoIndexService(index_root=tmp_path / "override")
    try:
        for service in (first, second):
            service.semantic_enabled = False
            service.enable(str(root), refresh_embedder=False)
            service.disable()
        result = first.purge()
        assert result["complete"], result
        assert result["removed_indexes"] == 2
        assert not (first.index_root / "index.db").exists()
        assert not (second.index_root / "index.db").exists()
        repeated = first.purge()
        assert repeated["status"] == "already-purged", repeated
    finally:
        first.disable()
        second.disable()


def test_unregistered_and_ignored_marker_cache_is_found(tmp_path, write_file, make_service):
    write_file(tmp_path / "main.py", "def item():\n    return 1\n")
    parent = make_service(tmp_path)
    directory = tmp_path / "ignored" / "deep" / ".auto-index-mcp"
    directory.parent.mkdir(parents=True)
    write_index_markers(directory, directory.parent)
    parent.configure_ignore(["ignored/"], mode="replace")
    result = parent.purge()
    assert result["complete"], result
    assert result["removed_indexes"] == 2
    assert not (directory / "marker.json").exists()


def test_unrelated_workspace_is_kept_running(tmp_path, write_file, make_service):
    for name in ("one", "other"):
        write_file(tmp_path / name / "main.py", "def item():\n    return 1\n")
    selected, unrelated = [make_service(tmp_path / name) for name in ("one", "other")]
    before = unrelated.store.db_path.read_bytes()
    assert selected.purge()["complete"]
    assert unrelated.enabled and unrelated.project.session_lease.held
    assert unrelated.store.db_path.read_bytes() == before
    assert unrelated.symbol_search("item")["items"]


def test_unknown_residue_is_tracked(tmp_path, write_file, make_service):
    write_file(tmp_path / "main.py", "def item():\n    return 1\n")
    service = make_service(tmp_path)
    unknown = service.index_root / "notes.txt"
    unknown.write_text("keep", encoding="utf-8")
    result = service.purge()
    assert result["status"] == "partial" and not result["complete"]
    assert unknown.read_text(encoding="utf-8") == "keep"
    assert IndexRegistry().get(service.index_root)["state"] == "residual"


def test_companion_disappears_between_presence_and_link_check(tmp_path, write_file, make_service, monkeypatch):
    write_file(tmp_path / "main.py", "def item():\n    return 1\n")
    service = make_service(tmp_path)
    companion = service.index_root / "index.db-wal"
    companion.write_bytes(b"temporary companion")
    original = discovery.is_link

    def unlink_before_check(path):
        if path == companion:
            companion.unlink(missing_ok=True)
        return original(path)

    monkeypatch.setattr(discovery, "is_link", unlink_before_check)
    result = service.purge()
    assert result["complete"] and result["removed_indexes"] == 1, result
