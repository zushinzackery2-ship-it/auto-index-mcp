from __future__ import annotations

import json
from pathlib import Path

import pytest

from auto_index_mcp.embedding.embedding_store import EmbeddingStore
from auto_index_mcp.embedding.indexer import SymbolEmbedder

# Chunked vector storage: long symbols carry one row per window, search folds
# them back to one hit per symbol, and incremental reuse works per window.

ALPHA_BODY = """def alpha(a):
    first_marker_line
    second_marker_line
    zebra_unique_needle
    return a
"""

BETA_BODY = """def beta(b):
    return b
"""


def _symbol(file_path: str, name: str, line: int, end_line: int, signature: str) -> dict:
    return {
        "file_path": file_path,
        "name": name,
        "kind": "function",
        "line": line,
        "end_line": end_line,
        "signature": signature,
        "complexity": 1,
    }


@pytest.fixture
def store(tmp_path: Path) -> EmbeddingStore:
    embedding_store = EmbeddingStore(tmp_path / "emb" / "embeddings.db")
    embedding_store.initialize()
    return embedding_store


@pytest.fixture
def project(tmp_path: Path, write_file) -> Path:
    root = tmp_path / "proj"
    write_file(root / "code.py", ALPHA_BODY + "\n" + BETA_BODY)
    return root


@pytest.fixture
def embedder(store: EmbeddingStore, windowed_embedder_cls) -> SymbolEmbedder:
    return SymbolEmbedder(windowed_embedder_cls(lines_per_window=4), store)


def _project_symbols() -> list[dict]:
    return [
        _symbol("code.py", "alpha", 1, 5, "def alpha(a):"),
        _symbol("code.py", "beta", 7, 8, "def beta(b):"),
    ]


def test_long_symbol_stores_one_row_per_window(embedder: SymbolEmbedder, project: Path) -> None:
    result = embedder.embed_project(project, _project_symbols())
    # alpha: head + 5 body lines -> 2 windows; beta: head + 2 lines -> 1 window.
    assert result["embedded"] == 3
    assert embedder.count() == 3
    assert embedder.count_symbols() == 2


def test_entries_are_keyed_per_window(embedder: SymbolEmbedder, project: Path, store: EmbeddingStore) -> None:
    embedder.embed_project(project, _project_symbols())
    with store.read_connect() as conn:
        entries = embedder.store.entries_for(conn, "code.py", embedder.model_key)
    assert set(entries) == {("alpha", 1, 0), ("alpha", 1, 1), ("beta", 7, 0)}


def test_search_returns_each_symbol_once(embedder: SymbolEmbedder, project: Path) -> None:
    embedder.embed_project(project, _project_symbols())
    hits = embedder.search("zebra_unique_needle", limit=10)
    names = [hit["symbol_name"] for hit in hits]
    assert names.count("alpha") == 1
    assert names[0] == "alpha"


def test_unchanged_windows_are_reused(embedder: SymbolEmbedder, project: Path) -> None:
    embedder.embed_project(project, _project_symbols())
    again = embedder.embed_files(
        project, {"code.py": _project_symbols()}
    )
    assert again["embedded"] == 0
    assert again["reused"] == 3


def test_tail_edit_reembeds_only_the_changed_window(
    embedder: SymbolEmbedder, project: Path, write_file
) -> None:
    embedder.embed_project(project, _project_symbols())
    edited = ALPHA_BODY.replace("return a", "return a + 1")
    write_file(project / "code.py", edited + "\n" + BETA_BODY)
    result = embedder.embed_files(project, {"code.py": _project_symbols()})
    assert result["embedded"] == 1
    assert result["reused"] == 2
    assert embedder.count() == 3


def test_window_shrink_leaves_no_stale_chunks(
    embedder: SymbolEmbedder, project: Path, write_file, store: EmbeddingStore
) -> None:
    embedder.embed_project(project, _project_symbols())
    write_file(project / "code.py", "def alpha(a):\n    return a\n\n" + BETA_BODY)
    symbols = [
        _symbol("code.py", "alpha", 1, 2, "def alpha(a):"),
        _symbol("code.py", "beta", 4, 5, "def beta(b):"),
    ]
    embedder.embed_files(project, {"code.py": symbols})
    assert embedder.count() == 2
    with store.read_connect() as conn:
        entries = embedder.store.entries_for(conn, "code.py", embedder.model_key)
    assert set(entries) == {("alpha", 1, 0), ("beta", 4, 0)}


def test_pre_chunk_table_is_recreated_on_initialize(tmp_path: Path) -> None:
    embedding_store = EmbeddingStore(tmp_path / "legacy" / "embeddings.db")
    with embedding_store.connect() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute(
            """
            CREATE TABLE symbol_embeddings (
                file_path TEXT NOT NULL,
                symbol_name TEXT NOT NULL,
                symbol_line INTEGER NOT NULL,
                model_name TEXT NOT NULL,
                text_hash TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT '',
                end_line INTEGER NOT NULL DEFAULT 0,
                signature TEXT NOT NULL DEFAULT '',
                complexity INTEGER NOT NULL DEFAULT 1,
                vector BLOB NOT NULL,
                PRIMARY KEY (file_path, symbol_name, symbol_line, model_name)
            )
            """
        )
        conn.execute(
            "INSERT INTO symbol_embeddings VALUES ('a.py', 'x', 1, 'old', 'h', '', 0, '', 1, x'00000000')"
        )
        conn.execute("INSERT INTO metadata VALUES ('version', ?)", (json.dumps(1),))

    embedding_store.initialize()

    with embedding_store.read_connect() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(symbol_embeddings)").fetchall()}
        count = conn.execute("SELECT COUNT(*) FROM symbol_embeddings").fetchone()[0]
        version = conn.execute("SELECT value FROM metadata WHERE key='version'").fetchone()[0]
    assert "chunk_index" in columns
    assert count == 0
    assert json.loads(version) == 2


def test_service_level_chunked_semantic_search_dedups(
    tmp_path: Path,
    write_file,
    install_embedder,
    make_service,
    wait_embedding,
    windowed_embedder_cls,
) -> None:
    project = tmp_path / "svc"
    write_file(project / "long.py", ALPHA_BODY + "\n" + BETA_BODY)
    install_embedder(windowed_embedder_cls(lines_per_window=4))
    service = make_service(project)
    wait_embedding(service)

    result = service.semantic_search("zebra_unique_needle", limit=10)
    names = [item["symbol_name"] for item in result["items"]]
    assert names.count("alpha") == 1

    status = service.embedding_status()
    assert status["vector_count"] == 3
    assert status["embedded_symbol_count"] == 2
