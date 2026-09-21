from __future__ import annotations

from typing import Any, Literal, Optional

from mcp.server.fastmcp import Context, FastMCP

from ..core.service import AutoIndexService
from .bootstrap import ensure_enabled
from .guard import run_service


def register_quality_tools(mcp: FastMCP, service: AutoIndexService) -> None:
    @mcp.tool()
    async def auto_index_quality_check(
        kind: Literal["nesting", "dangling", "all"] = "nesting",
        max_depth: int = 4,
        languages: list[str] | None = None,
        include_low_confidence: bool = False,
        include_tests: bool = False,
        limit: int = 50,
        exclude_paths: list[str] | None = None,
        active_only: bool = False,
        ctx: Optional[Context] = None,
    ) -> dict[str, Any]:
        """Static quality findings from the persisted index (no LSP needed).

        kind="nesting" flags over-deep nesting (tune ``max_depth``);
        kind="dangling" flags likely-unused and unreachable code (heuristic:
        functions only passed as values can false-positive, non-Python
        unreachable detection is medium confidence); kind="all" runs both.
        Useful before refactors and in review sweeps, not for navigation.
        """
        blocked = await ensure_enabled(service, ctx)
        if blocked is not None:
            return blocked

        def _run() -> dict[str, Any]:
            nesting = None
            dangling = None
            if kind in ("nesting", "all"):
                nesting = service.nesting_check(max_depth, languages, limit, exclude_paths, active_only)
            if kind in ("dangling", "all"):
                dangling = service.dangling_check(include_low_confidence, include_tests, limit, exclude_paths, active_only)
            if kind == "nesting":
                return nesting  # type: ignore[return-value]
            if kind == "dangling":
                return dangling  # type: ignore[return-value]
            return {"format": "auto_index_quality_check_v2", "nesting": nesting, "dangling": dangling}

        return await run_service(service, _run)
