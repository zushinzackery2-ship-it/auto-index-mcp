from __future__ import annotations

from typing import Any, Literal

from mcp.server.fastmcp import FastMCP

from ..core.service import AutoIndexService


def register_navigation_tools(mcp: FastMCP, service: AutoIndexService) -> None:
    @mcp.resource("files://{file_path}")
    def get_file_content(file_path: str) -> str:
        """Return the content of a project file."""
        return service.file_content(file_path)

    @mcp.tool()
    def auto_index_overview(limit: int = 30) -> dict[str, Any]:
        """Return a compact codebase overview for first-pass context gathering."""
        return service.overview(limit)

    @mcp.tool()
    def auto_index_tree_get(root_path: str = "", depth: int = 2, limit: int = 120) -> dict[str, Any]:
        """Return a compact folder tree with file counts, language mix, and samples."""
        return service.tree_get(root_path, depth, limit)

    @mcp.tool()
    def auto_index_query(
        text: str = "",
        languages: list[str] | None = None,
        parent: str = "",
        limit: int = 80,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """Query indexed files by text, language, parent path, and cursor."""
        return service.query(text, languages, parent, limit, cursor)

    @mcp.tool()
    def auto_index_file(path: str, detail: Literal["summary", "full"] = "summary") -> dict[str, Any]:
        """Return one indexed file record.

        ``detail="summary"`` (default) returns imports, symbols and lightweight
        complexity; ``detail="full"`` returns the complete persisted file record.
        """
        return service.file_summary(path) if detail == "summary" else service.get(path)

    @mcp.tool()
    def auto_index_resolve_path(path: str, limit: int = 20) -> dict[str, Any]:
        """Resolve a fuzzy file name or path into indexed candidates."""
        return service.resolve_path(path, limit)

    @mcp.tool()
    def auto_index_diff_filesystem() -> dict[str, Any]:
        """Compare the persisted index with the current filesystem."""
        return service.diff_filesystem()

