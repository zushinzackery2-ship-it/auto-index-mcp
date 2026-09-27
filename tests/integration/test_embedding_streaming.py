from __future__ import annotations

import tracemalloc

from auto_index_mcp.embedding.backend import BagHashEmbedder
from auto_index_mcp.storage.embeddings import EmbeddingStore
from auto_index_mcp.embedding.indexer import SymbolEmbedder


def test_warm_embedding_retains_only_bounded_file_batches(tmp_path, write_file):
    store = EmbeddingStore(tmp_path / "vectors.db")
    store.initialize()
    indexer = SymbolEmbedder(BagHashEmbedder(), store)
    symbols = []
    for index in range(900):
        path = f"f{index:04}.py"
        write_file(tmp_path / path, "def compute(rows):\n    return sum(rows)\n")
        symbols.append(dict(file_path=path, name="compute", line=1, end_line=2, kind="function"))
    first = indexer.embed_project(tmp_path, symbols)
    assert first["embedded"] == 900
    tracemalloc.start()
    try:
        result = indexer.embed_project(tmp_path, symbols)
        _, peak = tracemalloc.get_traced_memory()
        assert result["reused"] == 900
        assert result["embedded"] == 0
        assert peak < 2 * 1024 * 1024
    finally:
        tracemalloc.stop()


def test_chunk_aggregation_across_query_batches(tmp_path):
    store = EmbeddingStore(tmp_path / "vectors.db")
    store.initialize()
    indexer = SymbolEmbedder(BagHashEmbedder(2), store)
    with store.connect() as conn:
        for index in range(260):
            indexer.store.replace_file(conn, f"f{index:04}.py", indexer.model_key, [dict(
                symbol_name="item", symbol_line=1, text_hash="h", vector=[0.5, 0.5],
            )])
        indexer.store.replace_file(conn, "f0255.py", indexer.model_key, [dict(
            symbol_name="item", symbol_line=1, chunk_index=index, text_hash="h",
            vector=[score, 0.0],
        ) for index, score in enumerate([0.3, 0.9, 0.6])])
    with store.read_connect() as conn:
        hits = indexer.store.search(conn, [1.0, 0.0], indexer.model_key, 5)
    assert hits[0]["file_path"] == "f0255.py"
    assert hits[0]["score"] == 0.9
    assert len({hit["file_path"] for hit in hits}) == 5
