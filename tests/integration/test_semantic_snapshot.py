"""Query markers and hits must belong to one vector database snapshot."""
from auto_index_mcp.core.service import AutoIndexService
from auto_index_mcp.embedding.backend import BagHashEmbedder
from auto_index_mcp.embedding.indexer import SymbolEmbedder
from auto_index_mcp.storage.vectors import SymbolEmbeddingStore


def test_vector_replacement_during_search_keeps_completion_and_hits_consistent(tmp_path, write_file, monkeypatch):
    write_file(tmp_path / "main.py", "def active():\n    return 1\n")
    service = AutoIndexService()
    service.semantic_enabled = False
    try:
        service.enable(str(tmp_path), refresh_embedder=False)
        service.semantic_enabled = True
        indexer = SymbolEmbedder(BagHashEmbedder(16), service.embedding_store)
        service.embedding_indexer = indexer
        service.embeddings._embed_project_exclusive(tmp_path, service.store, indexer, 0)
        original = SymbolEmbeddingStore.search

        def replace_then_search(vector_store, conn, *args, **kwargs):
            with service.embedding_store.connect() as writer:
                vector_store.delete_file(writer, "main.py", indexer.model_key)
            return original(vector_store, conn, *args, **kwargs)

        monkeypatch.setattr(SymbolEmbeddingStore, "search", replace_then_search)
        result = service.semantic_search("active", min_score=-1)
        assert result["items"] or not result["complete"]
        assert result["sources"][0]["vector_count"] == len(result["items"])
    finally:
        service.disable()
