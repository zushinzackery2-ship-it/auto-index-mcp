from __future__ import annotations

from typing import Any, Literal

from mcp.server.fastmcp import FastMCP

from ..core.config import DEFAULT_WATCH_DEBOUNCE_SECONDS
from ..core.service import AutoIndexService


def register_lifecycle_tools(mcp: FastMCP, service: AutoIndexService) -> None:
    @mcp.tool()
    def auto_index_enable(root_path: str, rebuild: bool = False, auto_watch: bool = True) -> dict[str, Any]:
        """Enable persistent code auto-indexing and optionally rebuild immediately."""
        result = service.enable_reusing_index(root_path, rebuild)
        if auto_watch:
            if service.can_start_auto_watch(result):
                result["watcher"] = service.start_watcher(wait_ready=False)
            elif result.get("status") == "indexing-in-background":
                service.request_auto_watch_after_build()
        return result

    @mcp.tool()
    def auto_index_disable() -> dict[str, Any]:
        """Disable auto-index state while keeping the persisted SQLite index."""
        return service.disable()

    @mcp.tool()
    def auto_index_status() -> dict[str, Any]:
        """Return index status, freshness, counts, and index location."""
        return service.status()

    @mcp.tool()
    def auto_index_ignore(
        mode: Literal["status", "add", "replace", "clear"] = "status",
        patterns: list[str] | None = None,
    ) -> dict[str, Any]:
        """View or configure runtime ignore patterns.

        ``.gitignore`` is loaded automatically from the active project root.
        Runtime patterns use gitignore-style syntax and affect the next rebuild
        or watcher start. ``mode="add"`` appends unique patterns,
        ``mode="replace"`` overwrites runtime patterns, and ``mode="clear"``
        removes runtime patterns.
        """
        return service.configure_ignore(patterns, mode)

    @mcp.tool()
    def auto_index_rebuild() -> dict[str, Any]:
        """Force a full source tree rebuild into the persisted index."""
        return service.rebuild()

    @mcp.tool()
    def auto_index_clear(delete_file: bool = False) -> dict[str, Any]:
        """Clear indexed data and optionally delete the SQLite file."""
        return service.clear(delete_file)

    @mcp.tool()
    def auto_index_watcher_start(debounce_seconds: float = DEFAULT_WATCH_DEBOUNCE_SECONDS) -> dict[str, Any]:
        """Start filesystem-event auto-refresh for the active root."""
        return service.start_watcher(debounce_seconds)

    @mcp.tool()
    def auto_index_watcher_stop() -> dict[str, Any]:
        """Stop filesystem-event auto-refresh."""
        return service.stop_watcher()
