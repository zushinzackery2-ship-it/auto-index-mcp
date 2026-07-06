from __future__ import annotations

from pathlib import Path

import pytest

from auto_index_mcp.embedding.backend import BagHashEmbedder
from auto_index_mcp.embedding.embedding_store import EmbeddingStore
from auto_index_mcp.embedding.indexer import TEXT_SCHEME_VERSION, SymbolEmbedder

# The storage key must identify the full text->vector mapping: backend name,
# backend fingerprint (when present) and the indexer text scheme. Any change
# purges stored vectors instead of silently mixing similarity spaces.


class FingerprintedEmbedder(BagHashEmbedder):
    def __init__(self, fingerprint: str, dim: int = 64) -> None:
        super().__init__(dim=dim)
        self._fingerprint = fingerprint

    @property
    def name(self) -> str:
        return "fingerprinted"

    @property
    def text_fingerprint(self) -> str:
        return self._fingerprint


@pytest.fixture
def store(tmp_path: Path) -> EmbeddingStore:
    embedding_store = EmbeddingStore(tmp_path / "embeddings.db")
    embedding_store.initialize()
    return embedding_store


@pytest.fixture
def project(tmp_path: Path, write_file) -> Path:
    root = tmp_path / "proj"
    write_file(root / "m.py", "def solo():\n    return 1\n")
    return root


SYMBOLS = [
    {
        "file_path": "m.py",
        "name": "solo",
        "kind": "function",
        "line": 1,
        "end_line": 2,
        "signature": "def solo():",
        "complexity": 1,
    }
]


def test_model_key_without_fingerprint_still_carries_text_scheme(store: EmbeddingStore) -> None:
    embedder = SymbolEmbedder(BagHashEmbedder(dim=64), store)
    assert embedder.model_key == f"baghash#{TEXT_SCHEME_VERSION}"


def test_model_key_joins_fingerprint_and_text_scheme(store: EmbeddingStore) -> None:
    embedder = SymbolEmbedder(FingerprintedEmbedder("maxlen=192"), store)
    assert embedder.model_key == f"fingerprinted#maxlen=192;{TEXT_SCHEME_VERSION}"


def test_fingerprint_change_purges_old_vectors(store: EmbeddingStore, project: Path) -> None:
    first = SymbolEmbedder(FingerprintedEmbedder("cfg=1"), store)
    first.embed_project(project, SYMBOLS)
    assert first.count() == 1

    second = SymbolEmbedder(FingerprintedEmbedder("cfg=2"), store)
    # A config change looks like a new model: nothing is reusable...
    assert second.count() == 0
    result = second.embed_project(project, SYMBOLS)
    assert result["embedded"] == 1
    assert result["reused"] == 0

    # ...and after the clean re-embed the old space is gone entirely.
    with store.read_connect() as conn:
        models = {
            row["model_name"]
            for row in conn.execute("SELECT DISTINCT model_name FROM symbol_embeddings").fetchall()
        }
    assert models == {second.model_key}
    assert first.count() == 0


def test_same_config_reuses_across_instances(store: EmbeddingStore, project: Path) -> None:
    first = SymbolEmbedder(FingerprintedEmbedder("cfg=1"), store)
    first.embed_project(project, SYMBOLS)
    second = SymbolEmbedder(FingerprintedEmbedder("cfg=1"), store)
    result = second.embed_project(project, SYMBOLS)
    assert result["embedded"] == 0
    assert result["reused"] == 1
