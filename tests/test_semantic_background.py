from __future__ import annotations

import threading
import time
from pathlib import Path

from auto_index_mcp.core.service import AutoIndexService
from auto_index_mcp.embedding.backend import BagHashEmbedder
from auto_index_mcp.embedding import indexer as embedding_indexer


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _make_project(root: Path) -> None:
    _write(root / "src" / "auth.py", "def authenticate_user(username, password):\n    return True\n")


def _make_model_dir(path: Path) -> Path:
    _write(path / "model.onnx", "fake model")
    _write(path / "tokenizer.json", "{}")
    return path


def test_embedding_status_does_not_load_model(monkeypatch, tmp_path: Path) -> None:
    _make_project(tmp_path)
    service = AutoIndexService(index_root=tmp_path / ".idx")
    service.enable(str(tmp_path), rebuild=False, refresh_embedder=False)
    monkeypatch.setattr(
        "auto_index_mcp.core.service_watcher.create_embedder",
        lambda env=None: (_ for _ in ()).throw(AssertionError("status must not load model")),
    )

    status = service.embedding_status()
    assert status["enabled"] is False
    assert status["model"] is None
    assert status["vector_count"] == 0
    # A build timer is always reported; idle before any embedding build runs.
    assert status["build_timer"]["running"] is False
    assert status["build_timer"]["elapsed_seconds"] is None


def test_semantic_search_starts_background_embedding_without_blocking(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _make_project(tmp_path)
    service = AutoIndexService(index_root=tmp_path / ".idx")
    service.enable(str(tmp_path), rebuild=True)
    service.embedding_indexer = None
    service.embedding_background = None
    model_dir = _make_model_dir(tmp_path / "model")
    started = threading.Event()
    release = threading.Event()

    def slow_create_embedder(env=None):
        _ = env
        started.set()
        release.wait(5.0)
        return BagHashEmbedder(dim=32)

    monkeypatch.setattr(
        "auto_index_mcp.core.service_semantic.resolve_embedding_model_path",
        lambda env=None: model_dir,
    )
    monkeypatch.setattr(
        "auto_index_mcp.core.service_watcher.create_embedder",
        slow_create_embedder,
    )

    start = time.perf_counter()
    result = service.semantic_search("authenticate user", limit=5)
    elapsed = time.perf_counter() - start

    assert elapsed < 1.0
    assert result["format"] == "auto_index_semantic_search_unavailable"
    assert "building" in result["error"]
    assert started.wait(1.0)
    release.set()
    assert service.embedding_background is not None
    assert service.embedding_background.wait(10.0)


def test_semantic_search_returns_partial_results_while_embedding_runs(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _write(tmp_path / "src" / "a_first.py", "def first_ready():\n    return True\n")
    _write(tmp_path / "src" / "b_second.py", "def second_waiting():\n    return True\n")
    second_entered = threading.Event()
    release = threading.Event()

    class BlockingEmbedder(BagHashEmbedder):
        @property
        def name(self) -> str:
            return "blocking"

        def embed(self, texts: list[str]) -> list[list[float]]:
            if any("def second_waiting" in text for text in texts):
                second_entered.set()
                release.wait(5.0)
            return super().embed(texts)

    monkeypatch.setattr(embedding_indexer, "EMBED_BATCH_SIZE", 1)
    monkeypatch.setattr(
        "auto_index_mcp.core.service_watcher.create_embedder",
        lambda env=None: BlockingEmbedder(dim=32),
    )
    service = AutoIndexService(index_root=tmp_path / ".idx")
    service.enable(str(tmp_path), rebuild=True)

    assert second_entered.wait(5.0)
    result = service.semantic_search("first ready", limit=5, min_score=-1.0)

    assert result["format"] == "auto_index_semantic_search"
    assert result["embedding"]["status"] == "partial"
    assert result["embedding"]["vector_count"] > 0
    assert result["embedding"]["total_symbol_count"] >= result["embedding"]["vector_count"]
    assert result["embedding"]["background"]["state"] == "running"
    assert any(item["symbol_name"] == "first_ready" for item in result["items"])

    release.set()
    assert service.embedding_background is not None
    assert service.embedding_background.wait(10.0)
