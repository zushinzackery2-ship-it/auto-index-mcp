"""Attachment, copied ownership and linked paths exercise the real cache API."""
import json
import shutil
import time

import pytest

from auto_index_mcp.cache.inventory import cache_inventory
from auto_index_mcp.cache.locking import lock_directory
from auto_index_mcp.cli import main
from auto_index_mcp.core.service import AutoIndexService
from auto_index_mcp.registry import IndexRegistry, registry_key
from auto_index_mcp.registry.markers import read_index_marker


def attach(root):
    service = AutoIndexService()
    service.semantic_enabled = False
    service.enable(str(root), rebuild=False, refresh_embedder=False)
    return service


def test_unregistered_active_cache_accepts_a_second_reader(tmp_path, write_file):
    write_file(tmp_path / "main.py", "def active():\n    return 1\n")
    first = attach(tmp_path)
    second = AutoIndexService()
    second.semantic_enabled = False
    try:
        first.rebuild_sync()
        first.registry.unregister(tmp_path)
        second.enable(str(tmp_path), rebuild=False, refresh_embedder=False)
        assert second.symbol_body("active")["code"].startswith("def active")
        assert first.store.db_path == second.store.db_path
        assert first.project.session_lease.held and second.project.session_lease.held
    finally:
        second.disable()
        first.disable()


def test_scan_can_register_active_cache_without_changing_ownership(tmp_path, write_file):
    write_file(tmp_path / "main.py", "def active():\n    return 1\n")
    service = attach(tmp_path)
    try:
        service.rebuild_sync()
        service.registry.unregister(tmp_path)
        before = (service.index_root / "marker.json").read_bytes()
        assert main(["clean", "--scan", str(tmp_path)]) == 0
        assert IndexRegistry().is_registered(tmp_path)
        assert (service.index_root / "marker.json").read_bytes() == before
        assert main(["clean", str(tmp_path), "-y"]) == 1
        assert service.symbol_body("active")["code"].startswith("def active")
    finally:
        service.disable()


def test_copy_gets_independent_cache_identity_while_original_is_active(tmp_path, write_file):
    original, copied = tmp_path / "original", tmp_path / "copied"
    write_file(original / "main.py", "def active():\n    return 1\n")
    first = attach(original)
    second = AutoIndexService()
    second.semantic_enabled = False
    try:
        first.rebuild_sync()
        # Close SQLite writes before copying; the shared session remains mounted.
        shutil.copytree(original, copied, ignore=shutil.ignore_patterns("logs"))
        marker = read_index_marker(first.index_root)
        second.enable(str(copied), rebuild=False, refresh_embedder=False)
        copied_marker = read_index_marker(second.index_root)
        assert copied_marker["index_id"] != marker["index_id"]
        assert lock_directory(first.index_root) != lock_directory(second.index_root)
        assert copied_marker["root"] == str(copied.resolve())
        assert read_index_marker(first.index_root) == marker
        assert second.symbol_body("active")["code"].startswith("def active")
        first.disable()
        assert main(["clean", str(original), "-y"]) == 0
        assert second.symbol_body("active")["code"].startswith("def active")
    finally:
        first.disable()
        second.disable()


def test_foreign_marker_rejected_before_any_database_creation(tmp_path, write_file):
    root, cache = tmp_path / "project", tmp_path / "index"
    write_file(root / "main.py", "def active():\n    return 1\n")
    write_file(cache / "marker.json", json.dumps(dict(tool="other-tool", root=str(root))))
    service = AutoIndexService(index_root=cache)
    service.semantic_enabled = False
    try:
        with pytest.raises(ValueError, match="ownership"):
            service.enable(str(root), rebuild=False, refresh_embedder=False)
        assert not (cache / "index.db").exists()
        assert json.loads((cache / "marker.json").read_text())["tool"] == "other-tool"
    finally:
        service.disable()


def test_linked_cache_root_is_reported_without_reading_external_data(tmp_path, write_file):
    external, linked = tmp_path / "external", tmp_path / "linked"
    write_file(external / "keepsake.bin", "x" * 500)
    try:
        linked.symlink_to(external, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symbolic links unavailable: {exc}")
    result = cache_inventory(linked)
    assert result["bytes"] == 0
    assert result["partial"]
    assert result["errors"]


def test_broken_registry_does_not_redirect_coordination(tmp_path, write_file, monkeypatch):
    write_file(tmp_path / "project" / "main.py", "def active():\n    return 1\n")
    blocker = tmp_path / "registry-file"
    blocker.write_text("occupied", encoding="utf-8")
    monkeypatch.delenv("AUTO_INDEX_CACHE_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("AUTO_INDEX_REGISTRY_DIR", str(blocker / "subdirectory"))
    service = attach(tmp_path / "project")
    try:
        assert lock_directory(service.index_root).is_relative_to(tmp_path / "cache")
        assert service.project.session_lease.held
        assert service.status()["registered"] is False
    finally:
        service.disable()


def test_dry_run_after_move_previews_actual_target_without_mutation(tmp_path, write_file, capsys):
    before, after = tmp_path / "before", tmp_path / "after"
    write_file(before / "main.py", "def active():\n    pass\n")
    service = attach(before)
    service.rebuild_sync()
    service.disable()
    before.rename(after)
    registry = IndexRegistry()
    registry_bytes = registry.path().read_bytes()
    marker_bytes = (after / ".auto-index-mcp" / "marker.json").read_bytes()
    assert main(["clean", str(after), "--dry-run"]) == 0
    output = capsys.readouterr().out
    assert "clean:" in output and str(after) in output
    assert registry.path().read_bytes() == registry_bytes
    assert (after / ".auto-index-mcp" / "marker.json").read_bytes() == marker_bytes


def test_move_rebinding_preserves_age_selection(tmp_path, write_file):
    before, after = tmp_path / "before", tmp_path / "after"
    write_file(before / "main.py", "def active():\n    pass\n")
    service = attach(before)
    service.rebuild_sync()
    directory = service.index_root
    service.disable()
    registry = IndexRegistry()
    data = registry._read()
    data["entries"][registry_key(directory)]["last_attached_at"] = time.time() - 90 * 86400
    registry._write(data)
    before.rename(after)
    assert main(["clean", str(after), "--older-than", "30d", "-y"]) == 0
    assert not (after / ".auto-index-mcp" / "index.db").exists()
