"""The default index covers all source directories without mounting child caches."""
from pathlib import Path

import pytest

from auto_index_mcp.core.service import AutoIndexService
from auto_index_mcp.registry.markers import write_index_markers
from auto_index_mcp.storage.index import IndexStore


def test_default_scans_nested_source_without_mounting_existing_child(tmp_path, write_file):
    root = tmp_path / "project"
    child = root / "pkg" / "nested"
    write_file(root / "main.py", "def main():\n    return 1\n")
    write_file(child / "child.py", "def child_value():\n    return 2\n")
    nested, parent = AutoIndexService(), AutoIndexService()
    try:
        nested.enable(str(child), refresh_embedder=False)
        result = parent.enable(str(root), refresh_embedder=False)
        assert result["child_index_count"] == 0
        assert result["file_count"] == result["total_file_count"] == 2
        assert parent.symbol_body("child_value")["path"] == "pkg/nested/child.py"
        assert Path(result["index_path"]) == root / ".auto-index-mcp" / "index.db"
    finally:
        parent.disable()
        nested.disable()


def test_default_never_runs_child_discovery(tmp_path, write_file, monkeypatch):
    write_file(tmp_path / "main.py", "def main():\n    pass\n")

    def forbidden(*args, **kwargs):
        raise AssertionError("default mode attempted child-index discovery")

    monkeypatch.setattr("auto_index_mcp.application.build_pipeline.discover_child_indexes", forbidden)
    monkeypatch.setattr("auto_index_mcp.indexing.snapshot.child_index_snapshot", forbidden)
    service = AutoIndexService()
    try:
        service.enable(str(tmp_path), refresh_embedder=False)
        service.sync_index_to_filesystem()
        assert service.status()["mount_child_indexes"] is False
    finally:
        service.disable()


def test_legacy_project_cache_is_still_reused_without_implicit_child_mounts(tmp_path, write_file, monkeypatch):
    root = tmp_path / "project"
    write_file(root / "main.py", "def current_source():\n    pass\n")
    legacy = root / ".auto-index-mcp"
    write_index_markers(legacy, root)
    store = IndexStore(legacy / "index.db")
    store.initialize()
    store.replace_all(str(root.resolve()), [], [])
    before = (legacy / "marker.json").read_bytes()
    monkeypatch.delenv("AUTO_INDEX_CACHE_MODE", raising=False)
    service = AutoIndexService()
    try:
        result = service.enable(str(root), refresh_embedder=False)
        assert Path(result["index_path"]).parent == legacy
        assert result["child_index_count"] == 0
        assert service.symbol_search("current_source")["items"]
        assert (legacy / "marker.json").read_bytes() == before
    finally:
        service.disable()


@pytest.mark.parametrize("override", [False, True])
def test_existing_cache_location_settings_remain_supported(tmp_path, write_file, monkeypatch, override):
    root = tmp_path / "project"
    write_file(root / "main.py", "VALUE = 1\n")
    directory = root / "cache"
    if not override:
        monkeypatch.setenv("AUTO_INDEX_CACHE_MODE", "user")
        monkeypatch.setenv("AUTO_INDEX_CACHE_DIR", str(directory))
    service = AutoIndexService(index_root=directory if override else None)
    try:
        result = service.enable(str(root), refresh_embedder=False)
        assert Path(result["index_path"]).is_relative_to(directory)
        assert service.status()["mount_child_indexes"] is False
    finally:
        service.disable()


def test_project_cache_environment_mode_remains_supported(tmp_path, write_file, monkeypatch):
    write_file(tmp_path / "main.py", "VALUE = 1\n")
    monkeypatch.setenv("AUTO_INDEX_CACHE_MODE", "project")
    service = AutoIndexService()
    try:
        result = service.enable(str(tmp_path), refresh_embedder=False)
        assert Path(result["index_path"]) == tmp_path / ".auto-index-mcp" / "index.db"
    finally:
        service.disable()
