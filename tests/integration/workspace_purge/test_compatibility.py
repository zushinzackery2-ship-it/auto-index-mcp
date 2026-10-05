"""Moved/copied caches, legacy coordination and explicit CLI recovery."""
from pathlib import Path
import shutil

import pytest

from auto_index_mcp.commands.build import run as build
from auto_index_mcp.registry.markers import write_index_markers
from auto_index_mcp.runtime.leases import BuildLock


def test_legacy_inline_lock_blocks_all_targets_and_remains_stable(tmp_path, make_service, write_file):
    root = tmp_path / "project"
    write_file(root / "main.py", "def item():\n    return 1\n")
    service = make_service(root)
    cache = root / "child" / ".auto-index-mcp"
    cache.mkdir(parents=True)
    (cache / "sessions.lock").write_bytes(b"")
    write_index_markers(cache, cache.parent)
    legacy = BuildLock(cache / "sessions.lock", resolve_cache=False)
    assert legacy.try_acquire()
    try:
        result = service.purge(timeout_seconds=1)
        assert result["status"] == "busy" and result["deleted_bytes"] == 0, result
        assert (service.index_root / "index.db").exists()
        assert (cache / "marker.json").exists()
    finally:
        legacy.release()
    assert service.purge()["complete"]
    assert (cache / "sessions.lock").exists()
    assert service.registry.get(cache)["state"] == "coordination"
    assert service.purge()["status"] == "already-purged"


def test_purge_after_move_cleans_all_nested_cache_aliases(tmp_path, make_service, write_file):
    before, after = tmp_path / "before", tmp_path / "after"
    write_file(before / "child" / "item.py", "def item():\n    return 1\n")
    child = make_service(before / "child")
    parent = make_service(before)
    child.disable()
    parent.disable()
    before.rename(after)
    moved = make_service(after)
    result = moved.purge()
    assert result["complete"] and result["removed_indexes"] == 3, result
    assert sum(row["deleted_bytes"] > 0 for row in result["results"]) == 2
    assert not (after / ".auto-index-mcp").exists()
    assert not (after / "child" / ".auto-index-mcp").exists()
    assert not moved.registry.strict_entries()


def test_copied_cache_has_separate_locks_from_live_original(tmp_path, make_service, write_file):
    root, source = tmp_path / "selected", tmp_path / "original"
    write_file(root / "main.py", "def selected():\n    return 1\n")
    write_file(source / "main.py", "def original():\n    return 1\n")
    selected, original = make_service(root), make_service(source)
    copy = root / "copy" / ".auto-index-mcp"
    shutil.copytree(original.index_root, copy)
    preview = selected.purge(dry_run=True)
    assert preview["selected_indexes"] == 2, preview
    result = selected.purge()
    assert result["complete"] and result["removed_indexes"] == 2, result
    assert not copy.exists()
    assert original.enabled and original.project.session_lease.held
    assert original.symbol_search("original")["items"]


def test_scope_scan_keeps_external_link_target(tmp_path, make_service, write_file):
    root, external = tmp_path / "selected", tmp_path / "external"
    write_file(root / "main.py", "def selected():\n    return 1\n")
    write_file(external / "main.py", "def external():\n    return 1\n")
    selected, other = make_service(root), make_service(external)
    try:
        (root / "linked").symlink_to(external, target_is_directory=True)
    except OSError as error:
        if getattr(error, "winerror", None) not in (1, 50):
            raise
        pytest.skip("selected filesystem does not support directory links")
    assert selected.purge()["complete"]
    assert other.enabled and Path(other.index_root / "index.db").exists()


def test_cli_build_explicitly_resumes_only_its_instance(tmp_path, make_service, write_file):
    write_file(tmp_path / "main.py", "def item():\n    return 1\n")
    service = make_service(tmp_path)
    assert service.purge()["complete"]
    assert build([str(tmp_path), "--no-semantic", "--quiet"]) == 0
    assert not service.enabled
    assert service.status()["suspension"] is not None
    assert (service.index_root / "index.db").exists()
