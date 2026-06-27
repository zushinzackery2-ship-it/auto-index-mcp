from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from ..core.service import AutoIndexService


def register_semantic_tools(mcp: FastMCP, service: AutoIndexService) -> None:
    @mcp.tool()
    def auto_index_semantic_search(
        query: str,
        limit: int = 10,
        min_score: float = 0.0,
    ) -> dict[str, Any]:
        """Natural-language semantic search over indexed symbols.

        Embeds the query and returns the most semantically similar symbols by
        cosine similarity, with file paths and line ranges. Uses the bundled
        ONNX model or ``AUTO_INDEX_EMBEDDING_MODEL`` when provided; without a
        usable model it reports unavailable instead of degrading to keyword
        search.
        """
        return service.semantic_search(query, limit, min_score)

    @mcp.tool()
    def auto_index_embedding_status() -> dict[str, Any]:
        """Report whether a semantic embedding backend is active and its vector count."""
        return service.embedding_status()
