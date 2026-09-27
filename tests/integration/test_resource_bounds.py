from __future__ import annotations

import sqlite3
import tracemalloc

import pytest

from auto_index_mcp.core.service import AutoIndexService
from auto_index_mcp.embedding.backend import BagHashEmbedder
from auto_index_mcp.storage.embeddings import EmbeddingStore
from auto_index_mcp.embedding.indexer import SymbolEmbedder
from auto_index_mcp.workspace.source import read_source


def test_tree_and_overview_do_not_load_full_project(tmp_path, write_file, monkeypatch):
    write_file(tmp_path / "a.py", "def alpha():\n    return 1\n")
    service = AutoIndexService()
    service.semantic_enabled = False
    try:
        service.enable(str(tmp_path))
        monkeypatch.setattr(service.store, "all_files", lambda: pytest.fail("navigation loaded all symbol graphs"))
        assert service.overview()["samples"][0]["symbols"] == ["alpha"]
        assert service.tree_get()["folders"][0]["file_count"] == 1
    finally:
        service.disable()


def test_incremental_delete_and_replace_rollback_together(tmp_path, write_file, monkeypatch):
    write_file(tmp_path / "a.py", "def alpha():\n    return 1\n")
    service = AutoIndexService()
    service.semantic_enabled = False
    try:
        service.enable(str(tmp_path))

        def fail(conn, records):
            raise sqlite3.OperationalError("injected write failure")

        monkeypatch.setattr("auto_index_mcp.storage.index.insert_many", fail)
        with pytest.raises(sqlite3.OperationalError):
            service.store.apply_files([], ["a.py"])
        assert service.store.symbol_count() == 1
        assert service.store.get_file("a.py") is not None
    finally:
        service.disable()


def test_source_cache_respects_byte_budget(tmp_path, write_file, monkeypatch):
    write_file(tmp_path / "long.txt", "x" * 100_000)
    lines, metadata = read_source(tmp_path, dict(path="long.txt"), read_limit=4096)
    assert metadata["read_bytes"] == 4096
    assert metadata["source_truncated"]
    assert len(lines[0]) == 4096


def test_vector_search_memory_is_bounded(tmp_path):
    pytest.importorskip("numpy")
    store = EmbeddingStore(tmp_path / "vectors.db")
    store.initialize()
    indexer = SymbolEmbedder(BagHashEmbedder(384), store)
    vector = indexer.backend.embed(["needle"])[0]
    with store.connect() as conn:
        for index in range(2400):
            indexer.store.replace_file(conn, f"f{index:05}.py", indexer.model_key, [dict(
                symbol_name="needle", symbol_line=1, text_hash="h", vector=vector,
            )])
    tracemalloc.start()
    try:
        hits = indexer.search("needle", 10)
        _, peak = tracemalloc.get_traced_memory()
        assert len(hits) == 10
        assert peak < 3 * 1024 * 1024
    finally:
        tracemalloc.stop()
