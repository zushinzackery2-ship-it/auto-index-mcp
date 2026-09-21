from __future__ import annotations

from typing import Any

from .background_indexer import timer_or_idle
from .service_state import ServiceBase
from ..embedding.backend import resolve_embedding_model_path
from ..embedding.indexer import SymbolEmbedder
from ..indexing.store import IndexStore


class ServiceSemanticMixin(ServiceBase):
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
        self._require_ready()
        if not query.strip():
            raise ValueError("query is required")
        if not self.semantic_enabled:
            return _unavailable("semantic search is disabled by AUTO_INDEX_SEMANTIC_MODE=off")
        if self.store is None:
            return _unavailable("embedding store is unavailable")
        indexer = self.embedding_indexer
        if indexer is None:
            if resolve_embedding_model_path() is None:
                return _unavailable(
                    "embedding model unavailable; install semantic dependencies "
                    "and keep models/minilm-onnx, or set "
                    "AUTO_INDEX_EMBEDDING_MODEL to an ONNX model directory"
                )
            return _building(self.ensure_embedding_background(), self.embedding_background)
        count = _embedding_vector_count(indexer)
        if not self._vectors_current(self.store, indexer):
            self.ensure_embedding_background()
        if count <= 0:
            return _building(self.ensure_embedding_background(), self.embedding_background)
        safe_limit = max(1, min(int(limit), 100))
        hits = indexer.search(query, safe_limit, min_score)
        embedding = self._partial_embedding_status(indexer, count)
        result: dict[str, Any] = {
            "format": "auto_index_semantic_search",
            "model": indexer.backend.name,
            "count": len(hits),
            "items": hits,
        }
        if embedding is not None:
            result["embedding"] = embedding
        return self._with_index_status(result)

    def embedding_status(self) -> dict[str, Any]:
        """Report whether a semantic embedding backend is active and its vector count."""
        indexer = self.embedding_indexer
        if indexer is None or self.store is None:
            result: dict[str, Any] = {"enabled": False, "model": None, "vector_count": 0}
            if self.embedding_background is not None:
                result["embedding_background"] = self.embedding_background.status()
            result["build_timer"] = timer_or_idle(self.embedding_background, None)
            return result
        try:
            count = indexer.count()
        except Exception as exc:
            return {
                "enabled": True,
                "model": indexer.backend.name,
                "vector_count": 0,
                "error": str(exc),
                "build_timer": timer_or_idle(self.embedding_background, None),
            }
        result = {
            "enabled": True,
            "model": indexer.backend.name,
            "vector_count": count,
            "embedded_symbol_count": _embedded_symbol_count(indexer),
        }
        if self.embedding_background is not None:
            result["embedding_background"] = self.embedding_background.status()
        result["build_timer"] = timer_or_idle(self.embedding_background, None)
        return result

    def _partial_embedding_status(self, indexer: SymbolEmbedder, vector_count: int) -> dict[str, Any] | None:
        background = self.embedding_background
        if background is None or not background.is_running() or self.store is None:
            return None
        # Long symbols carry several window vectors, so progress compares the
        # distinct embedded symbols against the index's symbol total; the raw
        # vector count is still reported for the storage-level view.
        return {
            "status": "partial",
            "vector_count": vector_count,
            "embedded_symbol_count": _embedded_symbol_count(indexer),
            "total_symbol_count": _symbol_count(self.store),
            "background": background.status(),
            "build_timer": background.timer(),
        }


def _embedding_vector_count(indexer: SymbolEmbedder) -> int:
    try:
        return indexer.count()
    except Exception:
        return 0


def _embedded_symbol_count(indexer: SymbolEmbedder) -> int:
    try:
        return indexer.count_symbols()
    except Exception:
        return 0


def _symbol_count(store: IndexStore) -> int:
    try:
        return store.symbol_count()
    except Exception:
        return 0


def _building(
    background_status: dict[str, Any],
    background: Any,
) -> dict[str, Any]:
    return {
        "format": "auto_index_semantic_search_unavailable",
        "error": "embedding vectors are building in the background",
        "items": [],
        "embedding_background": background_status,
        "build_timer": timer_or_idle(background, None),
    }


def _unavailable(error: str) -> dict[str, Any]:
    return {
        "format": "auto_index_semantic_search_unavailable",
        "error": error,
        "items": [],
    }
