from __future__ import annotations

import math
from pathlib import Path

import pytest

from auto_index_mcp.core.service import AutoIndexService
from auto_index_mcp.embedding import backend as embedding_backend
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


def _make_model_dir(path: Path) -> Path:
    _write(path / "model.onnx", "fake model")
    _write(path / "tokenizer.json", "{}")
    return path


def _install_baghash(monkeypatch, dim: int = 128) -> None:
    monkeypatch.setattr(
        "auto_index_mcp.core.service_watcher.create_embedder",
        lambda env=None: BagHashEmbedder(dim=dim),
    )


def _wait_embedding(service: AutoIndexService) -> dict:
    assert service.embedding_background is not None
    assert service.embedding_background.wait(10.0) is True
    status = service.embedding_background.status()
    assert status["state"] == "done"
    assert status["last_result"] is not None
    return status["last_result"]


@pytest.fixture(autouse=True)
def _clear_embedder_cache():
    embedding_backend._EMBEDDER_CACHE.clear()
    yield
    embedding_backend._EMBEDDER_CACHE.clear()


def test_embedding_model_path_uses_bundled_when_env_unset(
    monkeypatch,
    tmp_path: Path,
) -> None:
    bundled = _make_model_dir(tmp_path / "bundled")
    monkeypatch.setattr(embedding_backend, "_find_bundled_model_dir", lambda: bundled)

    assert embedding_backend.resolve_embedding_model_path({}) == bundled


def test_embedding_model_path_env_overrides_bundled(
    monkeypatch,
    tmp_path: Path,
) -> None:
    bundled = _make_model_dir(tmp_path / "bundled")
    explicit = _make_model_dir(tmp_path / "explicit")
    monkeypatch.setattr(embedding_backend, "_find_bundled_model_dir", lambda: bundled)

    env = {"AUTO_INDEX_EMBEDDING_MODEL": str(explicit)}
    assert embedding_backend.resolve_embedding_model_path(env) == explicit


def test_embedding_model_path_invalid_env_does_not_fallback(
    monkeypatch,
    tmp_path: Path,
) -> None:
    bundled = _make_model_dir(tmp_path / "bundled")
    monkeypatch.setattr(embedding_backend, "_find_bundled_model_dir", lambda: bundled)

    assert (
        embedding_backend.resolve_embedding_model_path(
            {"AUTO_INDEX_EMBEDDING_MODEL": str(tmp_path / "missing")}
        )
        is None
    )


def test_embedding_model_path_missing_bundled_returns_none(monkeypatch) -> None:
    monkeypatch.setattr(embedding_backend, "_find_bundled_model_dir", lambda: None)

    assert embedding_backend.resolve_embedding_model_path({}) is None


@pytest.mark.allow_default_embedder
def test_bundled_model_enables_service_embeddings(monkeypatch, tmp_path: Path) -> None:
    class FakeOnnxEmbedder:
        def __init__(self, model_dir: Path) -> None:
            self.model_dir = Path(model_dir)
            self._backend = BagHashEmbedder(dim=32)

        @property
        def dim(self) -> int:
            return 32

        @property
        def name(self) -> str:
            return f"fake-{self.model_dir.name}"

        def embed(self, texts: list[str]) -> list[list[float]]:
            return self._backend.embed(texts)

    model_dir = _make_model_dir(tmp_path / "model")
    project = tmp_path / "project"
    _make_project(project)
    monkeypatch.setattr(embedding_backend, "_find_bundled_model_dir", lambda: model_dir)
    monkeypatch.setattr("auto_index_mcp.embedding.onnx_backend.OnnxEmbedder", FakeOnnxEmbedder)

    service = AutoIndexService(index_root=tmp_path / ".idx")
    rebuild = service.enable(str(project), rebuild=True)

    assert rebuild["embedding"] is not None
    assert rebuild["embedding"]["model"] == "fake-model"
    assert rebuild["embedding"]["status"] == "embedding-in-background"
    assert _wait_embedding(service)["embedded"] > 0


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


def test_semantic_search_unavailable_without_model(monkeypatch, tmp_path: Path) -> None:
    _make_project(tmp_path)
    monkeypatch.setattr(
        "auto_index_mcp.core.service_semantic.resolve_embedding_model_path",
        lambda env=None: None,
    )
    service = AutoIndexService(index_root=tmp_path / ".idx")
    service.enable(str(tmp_path), rebuild=True)
    result = service.semantic_search("authenticate user")
    assert result["items"] == []
    assert "unavailable" in result["error"]


def test_semantic_search_end_to_end(monkeypatch, tmp_path: Path) -> None:
    _install_baghash(monkeypatch)
    _make_project(tmp_path)
    service = AutoIndexService(index_root=tmp_path / ".idx")
    rebuild = service.enable(str(tmp_path), rebuild=True)
    embedding = _wait_embedding(service)

    assert rebuild["embedding"] is not None
    assert embedding["embedded"] > 0

    status = service.embedding_status()
    assert status["enabled"] is True
    assert status["vector_count"] > 0

    result = service.semantic_search("authenticate user login", limit=5)
    assert result["format"] == "auto_index_semantic_search"
    assert result["count"] > 0
    top_paths = [item["file_path"] for item in result["items"]]
    assert any("auth" in path for path in top_paths), top_paths
    assert result["items"][0]["score"] >= result["items"][-1]["score"]


def test_embedding_batches_pending_symbols_across_files(monkeypatch, tmp_path: Path) -> None:
    class CountingEmbedder(BagHashEmbedder):
        def __init__(self) -> None:
            super().__init__(dim=32)
            self.batch_sizes: list[int] = []

        def embed(self, texts: list[str]) -> list[list[float]]:
            self.batch_sizes.append(len(texts))
            return super().embed(texts)

    backend = CountingEmbedder()
    monkeypatch.setattr(
        "auto_index_mcp.core.service_watcher.create_embedder",
        lambda env=None: backend,
    )
    _make_project(tmp_path)
    service = AutoIndexService(index_root=tmp_path / ".idx")
    service.enable(str(tmp_path), rebuild=True)
    _wait_embedding(service)

    assert backend.batch_sizes == [7]


def test_text_hash_reuse_on_rebuild(monkeypatch, tmp_path: Path) -> None:
    _install_baghash(monkeypatch)
    _make_project(tmp_path)
    service = AutoIndexService(index_root=tmp_path / ".idx")
    service.enable(str(tmp_path), rebuild=True)
    first_result = _wait_embedding(service)
    first_embedded = first_result["embedded"]
    first_reused = first_result["reused"]

    second = service.rebuild_sync()
    second_result = _wait_embedding(service)
    assert second["embedding"]["status"] == "embedding-in-background"
    assert second_result["reused"] == first_embedded + first_reused
    assert second_result["embedded"] == 0


def test_incremental_embed_files(monkeypatch, tmp_path: Path) -> None:
    _install_baghash(monkeypatch)
    _make_project(tmp_path)
    service = AutoIndexService(index_root=tmp_path / ".idx")
    service.enable(str(tmp_path), rebuild=True)
    _wait_embedding(service)
    total = service.embedding_status()["vector_count"]
    assert total > 0

    new_file = tmp_path / "src" / "token.py"
    _write(new_file, "def issue_access_token(user):\n    return token\n")
    service.rebuild_sync()
    _wait_embedding(service)
    assert service.embedding_status()["vector_count"] > total


def test_search_score_ordering(monkeypatch, tmp_path: Path) -> None:
    _install_baghash(monkeypatch)
    _make_project(tmp_path)
    service = AutoIndexService(index_root=tmp_path / ".idx")
    service.enable(str(tmp_path), rebuild=True)
    _wait_embedding(service)
    result = service.semantic_search("authenticate user login", limit=10, min_score=-1.0)
    scores = [item["score"] for item in result["items"]]
    assert scores == sorted(scores, reverse=True)


def test_symbol_embedder_rejects_empty_query(monkeypatch, tmp_path: Path) -> None:
    _install_baghash(monkeypatch)
    _make_project(tmp_path)
    service = AutoIndexService(index_root=tmp_path / ".idx")
    service.enable(str(tmp_path), rebuild=True)
    _wait_embedding(service)
    with pytest.raises(ValueError):
        service.semantic_search("   ")
