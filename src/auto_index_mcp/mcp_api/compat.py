from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from ..compatibility.code_index import CompatService
from ..core.service import AutoIndexService


def register_compat_tools(mcp: FastMCP, service: AutoIndexService, compat: CompatService) -> None:
    @mcp.tool()
    def set_project_path(path: str) -> str:
        """Compatibility tool: initialize indexing for a project directory."""
        result = compat.set_project_path(path)
        service.start_watcher()
        return f"{result} Auto-refresh is running."

    @mcp.tool()
    def find_files(pattern: str) -> list[str]:
        """Compatibility tool: find indexed files by glob or filename."""
        return compat.find_files(pattern)

    @mcp.tool()
    def get_file_summary(file_path: str) -> dict[str, Any]:
        """Compatibility tool: summarize one indexed file."""
        return compat.get_file_summary(file_path)

    @mcp.tool()
    def get_symbol_body(file_path: str, symbol_name: str) -> dict[str, Any]:
        """Compatibility tool: return one symbol source body."""
        return compat.get_symbol_body(file_path, symbol_name)

    @mcp.tool()
    def search_code_advanced(
        pattern: str,
        case_sensitive: bool = True,
        context_lines: int = 0,
        file_pattern: str | None = None,
        fuzzy: bool = False,
        regex: bool | None = None,
        start_index: int = 0,
        max_results: int | None = 10,
    ) -> dict[str, Any]:
        """Compatibility tool: search code with pagination and optional filtering."""
        return compat.search_code_advanced(
            pattern,
            case_sensitive,
            context_lines,
            file_pattern,
            fuzzy,
            regex,
            start_index,
            max_results,
        )
