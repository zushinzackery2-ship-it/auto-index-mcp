from __future__ import annotations

from typing import Any, Protocol, cast

from ..embedding.indexer import SymbolEmbedder
from ..indexing.store import IndexStore


class _SemanticService(Protocol):
    root_path: Any
    store: IndexStore | None
    embedding_indexer: SymbolEmbedder | None

    def _require_ready(self) -> None:
        ...

    def _with_index_status(self, result: dict[str, Any]) -> dict[str, Any]:
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
        indexer = service.embedding_indexer
        if indexer is None or service.store is None:
            return {
                "format": "auto_index_semantic_search_unavailable",
                "error": (
                    "embedding model unavailable; install semantic dependencies "
                    "and keep models/minilm-onnx, or set "
                    "AUTO_INDEX_EMBEDDING_MODEL to an ONNX model directory"
                ),
                "items": [],
            }
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
            return {"enabled": False, "model": None, "vector_count": 0}
        try:
            count = indexer.count(service.store)
        except Exception as exc:
            return {
                "enabled": True,
                "model": indexer.backend.name,
                "vector_count": 0,
                "error": str(exc),
            }
        return {"enabled": True, "model": indexer.backend.name, "vector_count": count}
