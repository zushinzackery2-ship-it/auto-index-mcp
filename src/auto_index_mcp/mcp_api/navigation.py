from __future__ import annotations

from typing import Any, Literal, Optional

from mcp.server.fastmcp import Context, FastMCP

from ..core.service import AutoIndexService
from .bootstrap import ensure_enabled
from .guard import run_service


def register_navigation_tools(mcp: FastMCP, service: AutoIndexService) -> None:
    @mcp.resource("files://{file_path}")
    def get_file_content(file_path: str) -> str:
        """Return the content of a project file."""
        return service.file_content(file_path)

    @mcp.tool()
    async def auto_index_overview(
        limit: int = 20,
        ctx: Optional[Context] = None,
    ) -> dict[str, Any]:
        """First look at an unknown codebase: language mix, top directories,
        and a directory-balanced sample of representative files (entry points
        first, test/archive dirs last). Cheaper than walking the tree; drill
        into a directory afterwards with auto_index_tree_get."""
        blocked = await ensure_enabled(service, ctx)
        if blocked is not None:
            return blocked
        return await run_service(service, service.overview, limit)

    @mcp.tool()
    async def auto_index_tree_get(
        dir: str = "",
        depth: int = 2,
        limit: int = 50,
        ctx: Optional[Context] = None,
    ) -> dict[str, Any]:
        """Folder-level summary under ``dir`` (project-relative, "" = root):
        per-folder file counts, language mix, and sample file names. Use to
        map structure before reading files; raise ``depth`` to split large
        folders further."""
        blocked = await ensure_enabled(service, ctx)
        if blocked is not None:
            return blocked
        return await run_service(service, service.tree_get, dir, depth, limit)

    @mcp.tool()
    async def auto_index_files(
        query: str = "",
        dir: str = "",
        languages: list[str] | None = None,
        limit: int = 20,
        cursor: str | None = None,
        ctx: Optional[Context] = None,
    ) -> dict[str, Any]:
        """Find files when the exact path is unknown. ``query`` matches path
        substrings, bare file names, globs (src/**/*.py), or an exact symbol
        name as fallback; combine with ``dir``/``languages`` filters. Empty
        query lists files under ``dir``. Returns compact rows with top symbol
        names."""
        blocked = await ensure_enabled(service, ctx)
        if blocked is not None:
            return blocked
        return await run_service(service, service.find_files, query, dir, languages, limit, cursor)

    @mcp.tool()
    async def auto_index_file(
        path: str,
        detail: Literal["summary", "full"] = "summary",
        ctx: Optional[Context] = None,
    ) -> dict[str, Any]:
        """One file's indexed record without reading the source: imports,
        symbols with line ranges, and complexity. ``path`` is tolerant
        (backslashes and absolute paths inside the project are normalized);
        misses return close candidates. detail="full" adds call-graph and
        nesting data per symbol."""
        blocked = await ensure_enabled(service, ctx)
        if blocked is not None:
            return blocked
        if detail == "summary":
            return await run_service(service, service.file_summary, path)
        return await run_service(service, service.get, path)
