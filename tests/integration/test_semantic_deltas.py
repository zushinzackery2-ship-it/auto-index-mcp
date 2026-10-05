"""Persistent deltas limit semantic I/O through restart and publication races."""
from unittest.mock import patch

from auto_index_mcp.core.service import AutoIndexService
from auto_index_mcp.embedding.backend import BagHashEmbedder
from auto_index_mcp.embedding.indexer import SymbolEmbedder
from auto_index_mcp.indexing.scanner import SourceScanner
from auto_index_mcp.storage.index import IndexStore
import auto_index_mcp.embedding.indexer as module


def prepare(root, write_file):
    for number in range(64):
        write_file(root / f"file{number:03}.py", f"def value_{number}():\n    return {number}\n")
    service = AutoIndexService()
    service.semantic_enabled = False
    service.enable(str(root), refresh_embedder=False)
    service.semantic_enabled = True
    indexer = SymbolEmbedder(BagHashEmbedder(16), service.embedding_store)
    service.embedding_indexer = indexer
    service.embeddings._embed_project_exclusive(root, service.store, indexer, 0.0)
    return service, indexer


def test_one_changed_file_reads_and_rewrites_only_one_file(tmp_path, write_file):
    service, indexer = prepare(tmp_path, write_file)
    try:
        source = tmp_path / "file000.py"
        write_file(source, "def value_0():\n    return 1000\n")
        service.store.apply_files([SourceScanner(str(tmp_path)).read_path(source)], [])
        with patch.object(module, "read_lines", wraps=module.read_lines) as reads:
            with patch.object(indexer.store, "replace_file", wraps=indexer.store.replace_file) as writes:
                result = service.embeddings._embed_project_exclusive(tmp_path, service.store, indexer, 0.0)
        assert reads.call_count == writes.call_count == 1
        assert result["files"] == 1
        assert indexer.count() == 64
    finally:
        service.disable()


def test_other_instance_delta_is_used_after_restart(tmp_path, write_file):
    service, _ = prepare(tmp_path, write_file)
    location = service.index_root
    service.disable()
    source = tmp_path / "file010.py"
    write_file(source, "def changed():\n    return 1000\n")
    writer = IndexStore(location / "index.db")
    writer.apply_files([SourceScanner(str(tmp_path)).read_path(source)], ["file011.py"])
    reader = AutoIndexService(index_root=location)
    reader.semantic_enabled = False
    try:
        reader.enable(str(tmp_path), rebuild=False, refresh_embedder=False)
        reader.semantic_enabled = True
        indexer = SymbolEmbedder(BagHashEmbedder(16), reader.embedding_store)
        with patch.object(module, "read_lines", wraps=module.read_lines) as reads:
            result = reader.embeddings._embed_project_exclusive(tmp_path, reader.store, indexer, 0.0)
        assert reads.call_count == 1
        assert result["files"] == 2
        assert indexer.count() == 63
    finally:
        reader.disable()


def test_publication_during_embedding_converges_without_full_rescan(tmp_path, write_file):
    service, indexer = prepare(tmp_path, write_file)
    original = module.read_lines
    source = tmp_path / "file000.py"
    write_file(source, "def value_0():\n    return 1000\n")
    service.store.apply_files([SourceScanner(str(tmp_path)).read_path(source)], [])
    published = False

    def read_then_publish(*args, **kwargs):
        nonlocal published
        result = original(*args, **kwargs)
        if not published:
            published = True
            changed = tmp_path / "file001.py"
            write_file(changed, "def newest():\n    return 2000\n")
            service.store.apply_files([SourceScanner(str(tmp_path)).read_path(changed)], [])
        return result

    try:
        with patch.object(module, "read_lines", side_effect=read_then_publish) as reads:
            service.embeddings._embed_project_exclusive(tmp_path, service.store, indexer, 0.0)
        assert reads.call_count <= 3
        assert indexer.count() == 64
        assert any(hit["symbol_name"] == "newest" for hit in indexer.search("newest", 64, -1))
    finally:
        service.disable()
