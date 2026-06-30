from __future__ import annotations

import sqlite3
from pathlib import Path

from auto_index_mcp.core.service import AutoIndexService
from auto_index_mcp.embedding import backend as embedding_backend
from auto_index_mcp.embedding.backend import BagHashEmbedder

AUTH_PY = '''def authenticate_user(username, password):
    return check_credentials(username, password)


def check_credentials(username, password):
    return True
'''

DB_PY = '''def open_database_connection(config):
    return ConnectionPool(config)
'''


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _make_project(root: Path) -> None:
    _write(root / "src" / "auth.py", AUTH_PY)
    _write(root / "src" / "db.py", DB_PY)


def _install_baghash(monkeypatch, dim: int = 64) -> None:
    monkeypatch.setattr(
        "auto_index_mcp.core.service_watcher.create_embedder",
        lambda env=None: BagHashEmbedder(dim=dim),
    )


def _wait_embedding(service: AutoIndexService) -> None:
    assert service.embedding_background is not None
    assert service.embedding_background.wait(10.0) is True
    assert service.embedding_background.status()["state"] == "done"


def _table_exists(db_path: Path, table: str) -> bool:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def _clear_cache():
    embedding_backend._EMBEDDER_CACHE.clear()


def test_vectors_persisted_in_separate_db(monkeypatch, tmp_path: Path) -> None:
    _clear_cache()
    _install_baghash(monkeypatch)
    project = tmp_path / "project"
    _make_project(project)
    service = AutoIndexService(index_root=tmp_path / ".idx")
    service.enable(str(project), rebuild=True)
    _wait_embedding(service)

    index_db = service.store.db_path
    embeddings_db = service.embedding_store.db_path
    assert index_db.name == "index.db"
    assert embeddings_db.name == "embeddings.db"
    assert embeddings_db.exists()
    # Vectors live only in the standalone db; the index db no longer carries them.
    assert not _table_exists(index_db, "symbol_embeddings")
    assert _table_exists(embeddings_db, "symbol_embeddings")
    assert service.embedding_status()["vector_count"] > 0
    _clear_cache()


def test_reuse_auto_builds_vectors_without_query(monkeypatch, tmp_path: Path) -> None:
    _clear_cache()
    _install_baghash(monkeypatch)
    project = tmp_path / "project"
    _make_project(project)
    index_root = tmp_path / ".idx"

    first = AutoIndexService(index_root=index_root)
    first.enable(str(project), rebuild=True)
    _wait_embedding(first)
    # Simulate a reused index whose vectors are missing (e.g. fresh embeddings.db).
    first.embedding_store.clear()
    assert first.embedding_status()["vector_count"] == 0

    second = AutoIndexService(index_root=index_root)
    second.enable_reusing_index(str(project))
    # No semantic_search call: reuse path must auto-dispatch the vector build.
    _wait_embedding(second)
    assert second.embedding_status()["vector_count"] > 0
    _clear_cache()


def test_clear_wipes_embedding_db(monkeypatch, tmp_path: Path) -> None:
    _clear_cache()
    _install_baghash(monkeypatch)
    project = tmp_path / "project"
    _make_project(project)
    service = AutoIndexService(index_root=tmp_path / ".idx")
    service.enable(str(project), rebuild=True)
    _wait_embedding(service)
    assert service.embedding_status()["vector_count"] > 0

    service.clear()
    assert service.embedding_status()["vector_count"] == 0
    _clear_cache()


def test_clear_delete_file_removes_embedding_db(monkeypatch, tmp_path: Path) -> None:
    _clear_cache()
    _install_baghash(monkeypatch)
    project = tmp_path / "project"
    _make_project(project)
    service = AutoIndexService(index_root=tmp_path / ".idx")
    service.enable(str(project), rebuild=True)
    _wait_embedding(service)
    embeddings_db = service.embedding_store.db_path
    assert embeddings_db.exists()

    service.clear(delete_file=True)
    assert not embeddings_db.exists()
    _clear_cache()


def test_search_independent_of_symbols_table(monkeypatch, tmp_path: Path) -> None:
    _clear_cache()
    _install_baghash(monkeypatch)
    project = tmp_path / "project"
    _make_project(project)
    service = AutoIndexService(index_root=tmp_path / ".idx")
    service.enable(str(project), rebuild=True)
    _wait_embedding(service)

    # Wipe the index symbols table: denormalized vectors must still answer search
    # with full metadata, proving no cross-table JOIN dependency remains.
    with service.store.connect() as conn:
        conn.execute("DELETE FROM symbols")

    result = service.semantic_search("authenticate user login", limit=5)
    assert result["count"] > 0
    top = result["items"][0]
    assert top["kind"]
    assert "signature" in top and "end_line" in top
    _clear_cache()
