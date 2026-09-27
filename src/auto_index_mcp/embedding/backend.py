from __future__ import annotations

import hashlib
import math
import os
import sys
import re
import threading
import logging
from weakref import WeakValueDictionary
from pathlib import Path
from typing import Protocol, runtime_checkable

EMBEDDING_DIM_DEFAULT = 384
_TOKEN_RE = re.compile(r"[a-z0-9_]+")
_BUNDLED_MODEL_DIR = Path("models") / "minilm-onnx"
_REQUIRED_MODEL_FILES = ("model.onnx", "tokenizer.json")
_EMBEDDING_THREADS_ENV = "AUTO_INDEX_EMBEDDING_THREADS"
_AUTO_THREAD_CAP = 1
_EMBEDDING_MAX_LENGTH_ENV = "AUTO_INDEX_EMBEDDING_MAX_LENGTH"
# Default picked from measured token coverage on real symbol texts (head +
# body slice): 64 fully covers ~15% of symbols, 128 ~48%, 192 ~67%, 256 ~78%.
# 192 is the knee of the coverage/cost curve; past it mean-pooling dilution
# grows faster than recall.
EMBEDDING_MAX_LENGTH_DEFAULT = 192
# MiniLM position embeddings top out at 512; below 16 the head alone no longer
# fits. Out-of-range overrides clamp instead of crashing ONNX at inference.
_EMBEDDING_MAX_LENGTH_MIN = 16
_EMBEDDING_MAX_LENGTH_MAX = 512


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


_EMBEDDER_CACHE: WeakValueDictionary = WeakValueDictionary()
_CACHE_LOCK = threading.Lock()


def _has_model_files(path: Path) -> bool:
    return path.is_dir() and all(
        (path / name).is_file() for name in _REQUIRED_MODEL_FILES
    )


def _find_bundled_model_dir() -> Path | None:
    for base in Path(__file__).resolve().parents:
        candidate = base / _BUNDLED_MODEL_DIR
        if _has_model_files(candidate):
            return candidate
    installed = Path(sys.prefix) / "share" / "auto-index-mcp" / _BUNDLED_MODEL_DIR
    if _has_model_files(installed):
        return installed
    return None


def resolve_embedding_model_path(env: dict[str, str] | None = None) -> Path | None:
    """Resolve the ONNX model directory from explicit config or bundled files."""
    environment = env if env is not None else os.environ
    model_path = environment.get("AUTO_INDEX_EMBEDDING_MODEL", "").strip()
    if model_path:
        path = Path(model_path)
        return path if _has_model_files(path) else None
    return _find_bundled_model_dir()


def resolve_embedding_threads(env: dict[str, str] | None = None) -> int:
    """Resolve the ONNX intra-op thread count for embedding inference.

    ``AUTO_INDEX_EMBEDDING_THREADS`` overrides explicitly: any value >= 1 is
    honored as-is, on the assumption the operator knows their host. Unset or
    invalid falls back to a polite background default capped at
    ``_AUTO_THREAD_CAP`` and never above ``cpu_count - 1`` so the user's
    foreground work always keeps a core. The small MiniLM encoder stops scaling
    past a few intra-op threads, so the cap costs no real throughput.
    """
    environment = env if env is not None else os.environ
    raw = environment.get(_EMBEDDING_THREADS_ENV, "").strip()
    if raw:
        try:
            requested = int(raw)
        except ValueError:
            requested = 0
        if requested >= 1:
            return requested
    cores = os.cpu_count() or 1
    return min(_AUTO_THREAD_CAP, max(1, cores - 1))


def resolve_embedding_max_length(env: dict[str, str] | None = None) -> int:
    """Resolve the tokenizer truncation window for symbol embedding texts.

    ``AUTO_INDEX_EMBEDDING_MAX_LENGTH`` overrides the measured default,
    clamped to what the encoder's position embeddings actually support.
    Unset or non-numeric values fall back to the default.
    """
    environment = env if env is not None else os.environ
    raw = environment.get(_EMBEDDING_MAX_LENGTH_ENV, "").strip()
    if raw:
        try:
            requested = int(raw)
        except ValueError:
            requested = 0
        if requested >= 1:
            return min(_EMBEDDING_MAX_LENGTH_MAX, max(_EMBEDDING_MAX_LENGTH_MIN, requested))
    return EMBEDDING_MAX_LENGTH_DEFAULT


def create_embedder(env: dict[str, str] | None = None) -> EmbeddingBackend | None:
    """Build an embedding backend from configuration.

    Resolution order:
      1. ``AUTO_INDEX_EMBEDDING_MODEL`` env var -> ONNX model directory.
      2. Bundled repo model at ``models/minilm-onnx``.
      3. Not set / model missing -> None (semantic search reports unavailable).

    No silent fallback to a fake embedder in production: callers receive None
    and must surface an explicit error.
    """
    path = resolve_embedding_model_path(env)
    if path is None:
        return None
    max_length = resolve_embedding_max_length(env)
    threads = resolve_embedding_threads(env)
    files = tuple((path / name).stat().st_mtime_ns for name in _REQUIRED_MODEL_FILES)
    cache_key = (path.resolve(), max_length, threads, files)
    with _CACHE_LOCK:
        return _cached_backend(cache_key, path, max_length, threads)


def _cached_backend(cache_key, path, max_length, threads):
    cached = _EMBEDDER_CACHE.get(cache_key)
    if cached is not None:
        return cached
    try:
        from .onnx_backend import OnnxEmbedder
    except ImportError:
        return None
    try:
        backend = OnnxEmbedder(
            path,
            max_length=max_length,
            intra_op_num_threads=threads,
        )
    except Exception:
        logging.getLogger(__name__).exception("embedding backend creation failed")
        return None
    _EMBEDDER_CACHE[cache_key] = backend
    return backend
