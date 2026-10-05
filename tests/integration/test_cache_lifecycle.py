"""Caches remain discoverable, attributable and removable through their lifecycle."""
import subprocess

from auto_index_mcp.cli import main
from auto_index_mcp.core.service import AutoIndexService
from auto_index_mcp.registry import IndexRegistry
from auto_index_mcp.registry.cleanup import index_dir_size
from auto_index_mcp.registry.markers import write_index_markers


def attach(root):
    service = AutoIndexService()
    service.semantic_enabled = False
    service.enable(str(root), refresh_embedder=False)
    return service


def test_scan_finds_nested_project_indexes(tmp_path, write_file, monkeypatch):
    monkeypatch.setenv("AUTO_INDEX_CACHE_MODE", "project")
    root = tmp_path / "workspace"
    child = root / "library"
    write_file(child / "child.py", "def child():\n    pass\n")
    write_file(root / "main.py", "def main():\n    pass\n")
    attach(child).disable()
    attach(root).disable()
    registry = IndexRegistry()
    registry.unregister(child)
    registry.unregister(root)
    assert len(registry.scan_and_adopt(tmp_path)) == 2
    assert registry.is_registered(child)


def test_clean_by_new_root_after_project_move(tmp_path, write_file, monkeypatch):
    monkeypatch.setenv("AUTO_INDEX_CACHE_MODE", "project")
    before, after = tmp_path / "before", tmp_path / "after"
    write_file(before / "main.py", "def value():\n    pass\n")
    attach(before).disable()
    before.rename(after)
    assert main(["clean", str(after), "-y"]) == 0
    assert not (after / ".auto-index-mcp" / "index.db").exists()
    assert not IndexRegistry().is_registered(before)
    assert not IndexRegistry().is_registered(after)


def test_corrupt_backup_is_included_in_cleanup(tmp_path, write_file, monkeypatch):
    monkeypatch.setenv("AUTO_INDEX_CACHE_MODE", "project")
    write_file(tmp_path / "main.py", "def value():\n    pass\n")
    directory = tmp_path / ".auto-index-mcp"
    directory.mkdir()
    (directory / "index.db").write_bytes(b"not a database" * 1000)
    attach(tmp_path).disable()
    assert list(directory.glob("*.corrupt-*"))
    assert main(["clean", str(tmp_path), "-y"]) == 0
    assert not list(directory.glob("*.corrupt-*"))


def test_unknown_residue_keeps_markers_and_registry(tmp_path, write_file, monkeypatch):
    monkeypatch.setenv("AUTO_INDEX_CACHE_MODE", "project")
    write_file(tmp_path / "main.py", "def value():\n    pass\n")
    service = attach(tmp_path)
    directory = service.index_root
    service.disable()
    write_file(directory / "keepsake.txt", "user data")
    assert main(["clean", str(tmp_path), "-y"]) == 0
    assert (directory / "keepsake.txt").read_text() == "user data"
    assert (directory / "marker.json").exists()
    assert IndexRegistry().get(tmp_path)["state"] == "residual"
    (directory / "keepsake.txt").unlink()
    assert main(["clean", str(tmp_path), "-y"]) == 0
    assert not IndexRegistry().is_registered(tmp_path)


def test_recursive_size_includes_logs_and_recovery(tmp_path, write_file):
    write_file(tmp_path / "logs" / "server-fixture.log", "x" * 1000)
    write_file(tmp_path / "recovery" / "index.db.corrupt-fixture", "x" * 500)
    assert index_dir_size(tmp_path) == 1500


def test_new_default_cache_does_not_write_to_source_tree(tmp_path, write_file, monkeypatch):
    monkeypatch.delenv("AUTO_INDEX_CACHE_MODE", raising=False)
    monkeypatch.setenv("AUTO_INDEX_CACHE_DIR", str(tmp_path / "cache"))
    root = tmp_path / "project"
    write_file(root / "main.py", "def value():\n    pass\n")
    original_files = {p.relative_to(root) for p in root.rglob("*")}
    service = attach(root)
    directory = service.index_root
    try:
        assert directory.is_relative_to(tmp_path / "cache" / "projects")
        assert {p.relative_to(root) for p in root.rglob("*")} == original_files
        assert not list(directory.glob("*.lock"))
        assert main(["clean", str(root), "-y"]) == 1
    finally:
        service.disable()
    assert main(["clean", str(root), "-y"]) == 0
    assert not directory.exists()


def test_project_cache_ignores_itself_in_git(tmp_path, write_file, monkeypatch):
    monkeypatch.setenv("AUTO_INDEX_CACHE_MODE", "project")
    write_file(tmp_path / "main.py", "def value():\n    pass\n")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, capture_output=True)
    attach(tmp_path).disable()
    result = subprocess.run(["git", "-C", str(tmp_path), "status", "--porcelain", "--", ".auto-index-mcp"],
                            check=True, capture_output=True, text=True)
    assert result.stdout == ""


def test_external_child_is_discovered_by_parent(tmp_path, write_file, monkeypatch):
    monkeypatch.setenv("AUTO_INDEX_CACHE_MODE", "user")
    monkeypatch.setenv("AUTO_INDEX_CACHE_DIR", str(tmp_path / "cache"))
    root = tmp_path / "project"
    write_file(root / "library" / "child.py", "def child_value():\n    return 1\n")
    write_file(root / "main.py", "def main():\n    pass\n")
    child = attach(root / "library")
    parent = attach(root)
    try:
        assert parent.status()["child_index_count"] == 1
        assert parent.symbol_body("child_value")["path"] == "library/child.py"
        child.disable()
        assert main(["clean", str(root / "library"), "-y"]) == 1
    finally:
        parent.disable()
        child.disable()


def test_scan_adopts_marker_only_residue(tmp_path):
    directory = tmp_path / "project" / ".auto-index-mcp"
    write_index_markers(directory, directory.parent)
    (directory / ("index.db.corrupt-" + "a" * 32)).write_bytes(b"corrupt")
    registry = IndexRegistry()
    assert len(registry.scan_and_adopt(tmp_path)) == 1
    assert registry.get(directory)["root"] == str(directory.parent.resolve())
