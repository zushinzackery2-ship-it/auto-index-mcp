from __future__ import annotations

from typing import Any, Optional

from mcp.server.fastmcp import Context, FastMCP

from ..core.service import AutoIndexService
from .bootstrap import ensure_enabled
from .guard import run_tool


def register_semantic_tools(mcp: FastMCP, service: AutoIndexService) -> None:
    @mcp.tool()
    async def auto_index_semantic_search(
        query: str,
        limit: int = 10,
        min_score: float = 0.0,
        ctx: Optional[Context] = None,
    ) -> dict[str, Any]:
        """Describe behavior in natural language and get the most relevant
        symbols ("where is retry backoff handled"). Use when you do not know
        the identifier; otherwise auto_index_symbol_search is more precise.
        Ranking blends vector cosine with name/signature overlap. The bundled
        MiniLM model is English-centric: phrase queries in English for best
        recall, or point AUTO_INDEX_EMBEDDING_MODEL at a multilingual ONNX
        model for Chinese. First call after a rebuild may report vectors
        still building; embedding state is visible in auto_index_status()."""
        blocked = await ensure_enabled(service, ctx)
        if blocked is not None:
            return blocked
        return run_tool(service.semantic_search, query, limit, min_score)
