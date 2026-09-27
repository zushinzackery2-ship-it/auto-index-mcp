from __future__ import annotations

from auto_index_mcp.core.service import AutoIndexService
from auto_index_mcp.embedding.backend import BagHashEmbedder
from auto_index_mcp.storage.completion import is_complete, mark_complete
from auto_index_mcp.storage.embeddings import EmbeddingStore
from auto_index_mcp.embedding.indexer import SymbolEmbedder


def test_model_roundtrip_retains_each_namespace(tmp_path):
    (tmp_path / "a.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
    database = EmbeddingStore(tmp_path / "embeddings.db")
    database.initialize()
    first = SymbolEmbedder(BagHashEmbedder(32), database)
    second = SymbolEmbedder(BagHashEmbedder(64), database)
    second.model_key += "-other"
    symbols = [dict(file_path="a.py", name="alpha", line=1, end_line=2, kind="function", signature="alpha")]
    for indexer in (first, second):
        indexer.embed_project(tmp_path, symbols)
        mark_complete(database, indexer.model_key, "source-1")
    assert first.count() == second.count() == 1
    assert is_complete(database, first.model_key, "source-1")
    first.delete_files(["a.py"])
    assert not is_complete(database, first.model_key, "source-1")
    assert second.count() == 1


def test_zero_vector_completion_is_a_valid_empty_result(tmp_path, install_embedder, wait_embedding):
    install_embedder(BagHashEmbedder(32))
    service = AutoIndexService()
    try:
        service.enable(str(tmp_path))
        wait_embedding(service)
        result = service.semantic_search("anything")
        assert result["format"] == "auto_index_semantic_search"
        assert result["state"] == "empty"
        assert result["complete"] is True
        assert result["items"] == []
    finally:
        service.disable()


def test_parent_semantic_search_includes_child_indexes(tmp_path, install_embedder, wait_embedding):
    child = tmp_path / "lib"
    child.mkdir()
    (child / "sample.py").write_text("def compute_total(rows):\n    return sum(rows)\n", encoding="utf-8")
    install_embedder(BagHashEmbedder(64))
    nested, parent = AutoIndexService(), AutoIndexService()
    try:
        nested.enable(str(child))
        wait_embedding(nested)
        parent.enable(str(tmp_path))
        wait_embedding(parent)
        result = parent.semantic_search("compute total", min_score=-1)
        assert result["state"] == "ready"
        assert result["items"][0]["file_path"] == "lib/sample.py"
        assert result["complete"] is True
    finally:
        parent.disable()
        nested.disable()
