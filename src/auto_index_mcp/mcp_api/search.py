from __future__ import annotations

from typing import Any, Literal, Optional

from mcp.server.fastmcp import Context, FastMCP

from ..core.service import AutoIndexService
from .bootstrap import ensure_enabled
from .guard import run_tool


def register_search_tools(mcp: FastMCP, service: AutoIndexService) -> None:
    @mcp.tool()
    async def auto_index_text_search(
        pattern: str,
        case_sensitive: bool = True,
        regex: bool = False,
        limit: int = 20,
        file_pattern: str | None = None,
        context_lines: int = 0,
        exclude_paths: list[str] | None = None,
        active_only: bool = False,
        ctx: Optional[Context] = None,
    ) -> dict[str, Any]:
        """Grep-style search over indexed source text (literal by default,
        regex=true for patterns). Scope with file_pattern glob or
        exclude_paths; context_lines>0 attaches surrounding lines. For
        identifier lookups prefer auto_index_symbol_search - it ranks
        definitions instead of every mention."""
        blocked = await ensure_enabled(service, ctx)
        if blocked is not None:
            return blocked
        return run_tool(
            service.text_search,
            pattern,
            case_sensitive,
            regex,
            limit,
            file_pattern,
            context_lines,
            exclude_paths,
            active_only,
        )

    @mcp.tool()
    async def auto_index_symbol_search(
        text: str = "",
        kind: str = "",
        limit: int = 20,
        cursor: str | None = None,
        ctx: Optional[Context] = None,
    ) -> dict[str, Any]:
        """Find where a function/class/method is defined. Results are ranked
        exact-name > prefix > substring > signature and fall back to
        camelCase/snake_case subtoken matching when nothing hits directly
        (``match_mode`` tells you which). Follow up with
        auto_index_symbol_body for source or auto_index_symbol_refs for
        callers."""
        blocked = await ensure_enabled(service, ctx)
        if blocked is not None:
            return blocked
        return run_tool(service.symbol_search, text, kind, limit, cursor)

    @mcp.tool()
    async def auto_index_symbol_body(
        symbol_name: str,
        path: str = "",
        line: int = 0,
        ctx: Optional[Context] = None,
    ) -> dict[str, Any]:
        """Source code of one symbol by exact name - the cheapest way to read
        a single function. ``path`` is optional: without it the whole index
        is searched, and same-name definitions come back as candidates to
        pick from (then pass path, plus line for duplicates inside a file).
        """
        blocked = await ensure_enabled(service, ctx)
        if blocked is not None:
            return blocked
        return run_tool(service.symbol_body, symbol_name, path, line)

    @mcp.tool()
    async def auto_index_symbol_refs(
        symbol_name: str,
        path: str = "",
        direction: Literal["callers", "callees", "both"] = "both",
        limit: int = 25,
        ctx: Optional[Context] = None,
    ) -> dict[str, Any]:
        """Call-graph for a symbol: who calls it (callers, as
        "file.py::caller" entries) and what it calls (callees). Use before
        changing a function to see its blast radius - this replaces manual
        grep for usages. ``path`` narrows same-name definitions."""
        blocked = await ensure_enabled(service, ctx)
        if blocked is not None:
            return blocked
        return run_tool(service.symbol_refs, symbol_name, path, direction, limit)
