from __future__ import annotations

import hashlib
import math
import os
import re
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

EMBEDDING_DIM_DEFAULT = 384
_TOKEN_RE = re.compile(r"[a-z0-9_]+")


@runtime_checkable
class EmbeddingBackend(Protocol):
    """Pluggable embedding backend contract.

    Backends MUST return L2-normalized vectors so cosine similarity reduces to
    a plain dot product at query time.
    """

    @property
    def dim(self) -> int:
        ...

    @property
    def name(self) -> str:
        ...

    def embed(self, texts: list[str]) -> list[list[float]]:
        ...


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


class BagHashEmbedder:
    """Deterministic bag-of-words hash embedder.

    Maps each token to a signed dimension bucket. Documents sharing tokens land
    on the same signed buckets, yielding non-trivial cosine similarity. This is
    NOT a real semantic model; it is a dependency-free test stand-in that lets
    the storage/retrieval/increment pipeline be exercised without a model file.
    """

    def __init__(self, dim: int = EMBEDDING_DIM_DEFAULT) -> None:
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def name(self) -> str:
        return "baghash"

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for text in texts:
            vec = [0.0] * self._dim
            for token in _tokenize(text):
                digest = hashlib.md5(token.encode("utf-8")).digest()
                index = int.from_bytes(digest[:2], "little") % self._dim
                sign = 1.0 if (digest[2] & 1) else -1.0
                vec[index] += sign
            norm = math.sqrt(sum(value * value for value in vec)) or 1.0
            out.append([value / norm for value in vec])
        return out


def create_embedder(env: dict[str, str] | None = None) -> EmbeddingBackend | None:
    """Build an embedding backend from configuration.

    Resolution order:
      1. ``AUTO_INDEX_EMBEDDING_MODEL`` env var -> ONNX model directory.
      2. Not set / model missing -> None (semantic search reports unavailable).

    No silent fallback to a fake embedder in production: callers receive None
    and must surface an explicit error.
    """
    environment = env if env is not None else os.environ
    model_path = environment.get("AUTO_INDEX_EMBEDDING_MODEL", "").strip()
    if not model_path:
        return None
    path = Path(model_path)
    if not path.exists():
        return None
    try:
        from .onnx_backend import OnnxEmbedder
    except ImportError:
        return None
    try:
        return OnnxEmbedder(path)
    except Exception:
        return None


def coerce_backend(value: Any) -> EmbeddingBackend:
    """Validate/normalize an externally-supplied backend (used by tests)."""
    if isinstance(value, EmbeddingBackend):
        return value
    raise TypeError("embedding backend must implement the EmbeddingBackend protocol")
