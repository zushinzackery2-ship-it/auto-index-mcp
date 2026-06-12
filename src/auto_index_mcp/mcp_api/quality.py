from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from ..core.service import AutoIndexService


def register_quality_tools(mcp: FastMCP, service: AutoIndexService) -> None:
    @mcp.tool()
    def auto_index_nesting_check(
        max_depth: int = 4,
        languages: list[str] | None = None,
        limit: int = 200,
        exclude_paths: list[str] | None = None,
        active_only: bool = False,
    ) -> dict[str, Any]:
        """Check indexed code nesting depth without starting an LSP server."""
        return service.nesting_check(max_depth, languages, limit, exclude_paths, active_only)

    @mcp.tool()
    def auto_index_dangling_check(
        include_low_confidence: bool = False,
        include_tests: bool = False,
        limit: int = 200,
        exclude_paths: list[str] | None = None,
        active_only: bool = False,
    ) -> dict[str, Any]:
        """Check likely dangling code using cheap static index signals."""
        return service.dangling_check(include_low_confidence, include_tests, limit, exclude_paths, active_only)
