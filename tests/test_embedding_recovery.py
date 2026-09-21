from __future__ import annotations

from auto_index_mcp.core.service import AutoIndexService
from auto_index_mcp.embedding.backend import BagHashEmbedder


def test_partial_vectors_are_completed_after_restart(tmp_path, write_file, install_embedder, wait_embedding):
    project = tmp_path / "project"
    write_file(project / "a.py", "def alpha():\n    return 1\n")
    write_file(project / "b.py", "def beta():\n    return 2\n")
    install_embedder(BagHashEmbedder(32))
    first = AutoIndexService(index_root=tmp_path / ".index")
    second = AutoIndexService(index_root=tmp_path / ".index")
    try:
        first.enable(str(project), rebuild=True)
        wait_embedding(first)
        first.embedding_store.clear()
        # A killed pass has committed file a but has not published completion.
        first.embedding_indexer.embed_files(project, dict([
            ("a.py", first.store.symbols_for_files(["a.py"]))
        ]))
        assert first.embedding_indexer.count() == 1
        second.enable_reusing_index(str(project))
        second.ensure_embedding_background()
        wait_embedding(second)
        assert second.embedding_indexer.count_symbols() == 2
    finally:
        first.disable()
        second.disable()


def test_rebuild_with_lazy_model_refreshes_existing_vectors(tmp_path, write_file, install_embedder, wait_embedding):
    project = tmp_path / "project"
    write_file(project / "a.py", "def before():\n    return 1\n")
    install_embedder(BagHashEmbedder(32))
    first = AutoIndexService(index_root=tmp_path / ".index")
    second = AutoIndexService(index_root=tmp_path / ".index")
    try:
        first.enable(str(project))
        wait_embedding(first)
        write_file(project / "a.py", "def after():\n    return 2\n")
        second.enable_reusing_index(str(project), rebuild=True, wait_seconds=5)
        second.ensure_embedding_background()
        wait_embedding(second)
        hits = second.semantic_search("after", min_score=-1)["items"]
        assert {hit["symbol_name"] for hit in hits} == {"after"}
    finally:
        first.disable()
        second.disable()
