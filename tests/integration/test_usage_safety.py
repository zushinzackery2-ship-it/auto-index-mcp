"""Usage and revision races reproduced through the public service boundary."""
from __future__ import annotations

import os

import pytest

from auto_index_mcp.cli import main
from auto_index_mcp.core.service import AutoIndexService
from auto_index_mcp.indexing.scanner import SourceScanner
from auto_index_mcp.mcp_api.guard import run_tool
from auto_index_mcp.storage.index import IndexStore
import auto_index_mcp.storage.index as index_module


def test_cleanup_preserves_attached_reader_without_watcher(tmp_path, write_file):
    write_file(tmp_path / "main.py", "def active():\n    return 1\n")
    service = AutoIndexService()
    service.semantic_enabled = False
    try:
        service.enable(str(tmp_path), refresh_embedder=False)
        assert main(["clean", str(tmp_path), "-y"]) == 1
        assert service.store.db_path.exists()
        assert service.symbol_body("active")["code"].startswith("def active")
        assert service.registry.is_registered(tmp_path)
    finally:
        service.disable()
    assert main(["clean", str(tmp_path), "-y"]) == 0


def test_clear_excludes_other_attached_sessions(tmp_path, write_file):
    write_file(tmp_path / "main.py", "def active():\n    return 1\n")
    services = [AutoIndexService(), AutoIndexService()]
    try:
        for service in services:
            service.semantic_enabled = False
            service.enable(str(tmp_path), rebuild=False, refresh_embedder=False)
        services[0].rebuild_sync()
        assert services[0].clear()["status"] == "clear-busy"
        assert services[1].symbol_body("active")["code"].startswith("def active")
        services[1].disable()
        assert services[0].clear()["file_count"] == 0
    finally:
        for service in services:
            service.disable()


def test_file_and_symbols_share_one_sqlite_snapshot(tmp_path, write_file, monkeypatch):
    source = tmp_path / "sample.py"
    write_file(source, "def before():\n    return 1\n")
    store = IndexStore(tmp_path / ".idx" / "index.db")
    store.initialize()
    old = SourceScanner(str(tmp_path)).read_path(source)
    store.replace_all(str(tmp_path), [old])
    write_file(source, "def after():\n    return 2\ndef added():\n    return 3\n")
    new = SourceScanner(str(tmp_path)).read_path(source)
    original = index_module.read_files

    def publish(conn, rows):
        store.apply_files([new], [])
        return original(conn, rows)

    monkeypatch.setattr(index_module, "read_files", publish)
    item = store.get_file("sample.py")
    assert item["sha1"] == old.sha1
    assert item["symbol_count"] == len(item["symbols"]) == 1
    assert item["symbols"][0]["name"] == "before"


@pytest.mark.parametrize("path", ["sample.py", ""])
def test_body_binds_symbol_to_its_original_source_revision(tmp_path, make_service, write_file, monkeypatch, path):
    source = tmp_path / "sample.py"
    write_file(source, "def target():\n    return 1\n")
    service = make_service(tmp_path)
    original = service.search._locate_symbols

    def locate_then_publish(name, selected_path):
        matches = original(name, selected_path)
        write_file(source, "def inserted():\n    return 2\n\ndef target():\n    return 3\n")
        service.store.apply_files([SourceScanner(str(tmp_path)).read_path(source)], [])
        return matches

    monkeypatch.setattr(service.search, "_locate_symbols", locate_then_publish)
    result = run_tool(service.symbol_body, "target", path=path)
    assert result["error"] == "source-changed"


def test_body_checks_content_even_when_stat_is_unchanged(tmp_path, make_service, write_file):
    source = tmp_path / "sample.py"
    write_file(source, "def target():\n    return 1\n")
    service = make_service(tmp_path)
    before = source.stat()
    write_file(source, "def target():\n    return 2\n")
    os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert run_tool(service.symbol_body, "target")["error"] == "source-changed"
