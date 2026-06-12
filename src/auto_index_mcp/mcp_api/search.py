from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from ..core.service import AutoIndexService


def register_search_tools(mcp: FastMCP, service: AutoIndexService) -> None:
    @mcp.tool()
    def auto_index_text_search(
        pattern: str,
        case_sensitive: bool = True,
        regex: bool = False,
        limit: int = 80,
        file_pattern: str | None = None,
        context_lines: int = 0,
        exclude_paths: list[str] | None = None,
        active_only: bool = False,
    ) -> dict[str, Any]:
        """Search indexed source text and return compact path/line matches."""
        return service.text_search(pattern, case_sensitive, regex, limit, file_pattern, context_lines, exclude_paths, active_only)

    @mcp.tool()
    def auto_index_symbol_search(
        text: str = "",
        kind: str = "",
        limit: int = 80,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """Search indexed symbols by name, signature, and optional symbol kind."""
        return service.symbol_search(text, kind, limit, cursor)

    @mcp.tool()
    def auto_index_symbol_body(path: str, symbol_name: str) -> dict[str, Any]:
        """Return the source body of a symbol from an indexed file."""
        return service.symbol_body(path, symbol_name)
