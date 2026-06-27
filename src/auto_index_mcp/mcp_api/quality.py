from __future__ import annotations

from typing import Any, Literal

from mcp.server.fastmcp import FastMCP

from ..core.service import AutoIndexService


def register_quality_tools(mcp: FastMCP, service: AutoIndexService) -> None:
    @mcp.tool()
    def auto_index_quality_check(
        kind: Literal["nesting", "dangling", "all"] = "nesting",
        max_depth: int = 4,
        languages: list[str] | None = None,
        include_low_confidence: bool = False,
        include_tests: bool = False,
        limit: int = 200,
        exclude_paths: list[str] | None = None,
        active_only: bool = False,
    ) -> dict[str, Any]:
        """Check indexed code quality without starting an LSP server.

        ``kind="nesting"`` reports over-deep nesting from the persisted
        ``symbol_nesting`` cache (uses ``max_depth``/``languages``).
        ``kind="dangling"`` reports likely dangling/unreachable code from the
        persisted ``quality_findings`` cache (uses
        ``include_low_confidence``/``include_tests``).
        ``kind="all"`` runs both and wraps them under ``nesting``/``dangling``
        keys. ``limit``/``exclude_paths``/``active_only`` apply to every mode.
        """
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
        return {"format": "auto_index_quality_check_v1", "nesting": nesting, "dangling": dangling}
