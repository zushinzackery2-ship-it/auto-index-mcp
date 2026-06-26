from __future__ import annotations

import math
from pathlib import Path

import pytest

from auto_index_mcp.core.service import AutoIndexService
from auto_index_mcp.embedding.backend import BagHashEmbedder
from auto_index_mcp.embedding.indexer import SymbolEmbedder
from auto_index_mcp.embedding.vector_store import decode_vector, encode_vector


AUTH_PY = '''def authenticate_user(username, password):
    return check_credentials(username, password)


def check_credentials(username, password):
    return True


class UserSession:
    def start(self):
        return None
'''

DB_PY = '''def open_database_connection(config):
    return ConnectionPool(config)


class ConnectionPool:
    def acquire(self):
        return None
'''


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _make_project(root: Path) -> None:
    _write(root / "src" / "auth.py", AUTH_PY)
    _write(root / "src" / "db.py", DB_PY)


def _install_baghash(monkeypatch, dim: int = 128) -> None:
    monkeypatch.setattr(
        "auto_index_mcp.core.service.create_embedder",
        lambda env=None: BagHashEmbedder(dim=dim),
    )


def test_vector_roundtrip() -> None:
    original = [0.1, -0.2, 0.3, 0.5]
    assert decode_vector(encode_vector(original)) == pytest.approx(original)


def test_baghash_is_normalized_and_deterministic() -> None:
    embedder = BagHashEmbedder(dim=64)
    first = embedder.embed(["authenticate user login"])[0]
    second = embedder.embed(["authenticate user login"])[0]
    assert first == pytest.approx(second)
    norm = math.sqrt(sum(v * v for v in first))
    assert norm == pytest.approx(1.0, abs=1e-5)


def test_baghash_shared_tokens_rank_higher() -> None:
    embedder = BagHashEmbedder(dim=128)
    auth_a = embedder.embed(["authenticate user login"])[0]
    auth_b = embedder.embed(["authenticate user session"])[0]
    database = embedder.embed(["database connection pool"])[0]

    def dot(a: list[float], b: list[float]) -> float:
        return sum(x * y for x, y in zip(a, b))

    assert dot(auth_a, auth_b) > dot(auth_a, database)


def test_semantic_search_unavailable_without_model(tmp_path: Path) -> None:
    _make_project(tmp_path)
    service = AutoIndexService(index_root=tmp_path / ".idx")
    service.enable(str(tmp_path), rebuild=True)
    result = service.semantic_search("authenticate user")
    assert result["items"] == []
    assert "not configured" in result["error"]


def test_semantic_search_end_to_end(monkeypatch, tmp_path: Path) -> None:
    _install_baghash(monkeypatch)
    _make_project(tmp_path)
    service = AutoIndexService(index_root=tmp_path / ".idx")
    rebuild = service.enable(str(tmp_path), rebuild=True)

    assert rebuild["embedding"] is not None
    assert rebuild["embedding"]["embedded"] > 0

    status = service.embedding_status()
    assert status["enabled"] is True
    assert status["vector_count"] > 0

    result = service.semantic_search("authenticate user login", limit=5)
    assert result["format"] == "auto_index_semantic_search"
    assert result["count"] > 0
    top_paths = [item["file_path"] for item in result["items"]]
    assert any("auth" in path for path in top_paths), top_paths
    assert result["items"][0]["score"] >= result["items"][-1]["score"]


def test_text_hash_reuse_on_rebuild(monkeypatch, tmp_path: Path) -> None:
    _install_baghash(monkeypatch)
    _make_project(tmp_path)
    service = AutoIndexService(index_root=tmp_path / ".idx")
    first = service.enable(str(tmp_path), rebuild=True)
    first_embedded = first["embedding"]["embedded"]
    first_reused = first["embedding"]["reused"]

    second = service.rebuild()
    assert second["embedding"]["reused"] == first_embedded + first_reused
    assert second["embedding"]["embedded"] == 0


def test_incremental_embed_files(monkeypatch, tmp_path: Path) -> None:
    _install_baghash(monkeypatch)
    _make_project(tmp_path)
    service = AutoIndexService(index_root=tmp_path / ".idx")
    service.enable(str(tmp_path), rebuild=True)
    total = service.embedding_status()["vector_count"]
    assert total > 0

    new_file = tmp_path / "src" / "token.py"
    _write(new_file, "def issue_access_token(user):\n    return token\n")
    service.rebuild()
    assert service.embedding_status()["vector_count"] > total


def test_search_score_ordering(monkeypatch, tmp_path: Path) -> None:
    _install_baghash(monkeypatch)
    _make_project(tmp_path)
    service = AutoIndexService(index_root=tmp_path / ".idx")
    service.enable(str(tmp_path), rebuild=True)
    result = service.semantic_search("authenticate user login", limit=10, min_score=-1.0)
    scores = [item["score"] for item in result["items"]]
    assert scores == sorted(scores, reverse=True)


def test_symbol_embedder_rejects_empty_query(monkeypatch, tmp_path: Path) -> None:
    _install_baghash(monkeypatch)
    _make_project(tmp_path)
    service = AutoIndexService(index_root=tmp_path / ".idx")
    service.enable(str(tmp_path), rebuild=True)
    with pytest.raises(ValueError):
        service.semantic_search("   ")
