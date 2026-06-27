from __future__ import annotations

from typing import Any, Protocol, cast

from .background_indexer import BackgroundIndexer
from ..embedding.backend import resolve_embedding_model_path
from ..embedding.indexer import SymbolEmbedder
from ..indexing.store import IndexStore


class _SemanticService(Protocol):
    root_path: Any
    store: IndexStore | None
    embedding_indexer: SymbolEmbedder | None
    embedding_background: BackgroundIndexer | None

    def _require_ready(self) -> None:
        ...

    def _with_index_status(self, result: dict[str, Any]) -> dict[str, Any]:
        ...

    def ensure_embedding_background(self) -> dict[str, Any]:
        ...


class ServiceSemanticMixin:
    def semantic_search(
        self,
        query: str,
        limit: int = 10,
        min_score: float = 0.0,
    ) -> dict[str, Any]:
        """Natural-language semantic search over indexed symbols.

        Embeds the query with the configured backend and returns the most
        similar symbols by cosine similarity. Uses the configured embedding
        backend or the bundled repo model; without one it reports unavailable
        rather than degrading to a fake result.
        """
        service = cast(_SemanticService, self)
        service._require_ready()
        if not query.strip():
            raise ValueError("query is required")
        if service.store is None:
            return _unavailable("embedding store is unavailable")
        indexer = service.embedding_indexer
        if indexer is None:
            if resolve_embedding_model_path() is None:
                return _unavailable(
                    "embedding model unavailable; install semantic dependencies "
                    "and keep models/minilm-onnx, or set "
                    "AUTO_INDEX_EMBEDDING_MODEL to an ONNX model directory"
                )
            return _building(service.ensure_embedding_background())
        count = _embedding_vector_count(service, indexer)
        if count <= 0:
            return _building(service.ensure_embedding_background())
        if _embedding_is_building(service, count):
            return _building(service.embedding_background.status())
        safe_limit = max(1, min(int(limit), 100))
        hits = indexer.search(service.store, query, safe_limit, min_score)
        return service._with_index_status(
            {
                "format": "auto_index_semantic_search",
                "model": indexer.backend.name,
                "count": len(hits),
                "items": hits,
            }
        )

    def embedding_status(self) -> dict[str, Any]:
        """Report whether a semantic embedding backend is active and its vector count."""
        service = cast(_SemanticService, self)
        indexer = service.embedding_indexer
        if indexer is None or service.store is None:
            result: dict[str, Any] = {"enabled": False, "model": None, "vector_count": 0}
            if service.embedding_background is not None:
                result["embedding_background"] = service.embedding_background.status()
            return result
        try:
            count = indexer.count(service.store)
        except Exception as exc:
            return {
                "enabled": True,
                "model": indexer.backend.name,
                "vector_count": 0,
                "error": str(exc),
            }
        result: dict[str, Any] = {
            "enabled": True,
            "model": indexer.backend.name,
            "vector_count": count,
        }
        if service.embedding_background is not None:
            result["embedding_background"] = service.embedding_background.status()
        return result


def _embedding_is_building(service: _SemanticService, count: int | None = None) -> bool:
    background = service.embedding_background
    if background is None or not background.is_running() or service.store is None:
        return False
    return count is None or count == 0


def _embedding_vector_count(service: _SemanticService, indexer: SymbolEmbedder) -> int:
    if service.store is None:
        return 0
    try:
        return indexer.count(service.store)
    except Exception:
        return 0


def _building(background_status: dict[str, Any]) -> dict[str, Any]:
    return {
        "format": "auto_index_semantic_search_unavailable",
        "error": "embedding vectors are building in the background",
        "items": [],
        "embedding_background": background_status,
    }


def _unavailable(error: str) -> dict[str, Any]:
    return {
        "format": "auto_index_semantic_search_unavailable",
        "error": error,
        "items": [],
    }
