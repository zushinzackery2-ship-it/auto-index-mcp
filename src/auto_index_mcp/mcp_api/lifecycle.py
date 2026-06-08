from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from ..core.config import DEFAULT_WATCH_DEBOUNCE_SECONDS
from ..core.service import AutoIndexService


def register_lifecycle_tools(mcp: FastMCP, service: AutoIndexService) -> None:
    @mcp.tool()
    def auto_index_enable(root_path: str, rebuild: bool = False, auto_watch: bool = True) -> dict[str, Any]:
        """Enable persistent code auto-indexing and optionally rebuild immediately."""
        result = service.enable_reusing_index(root_path, rebuild)
        if auto_watch and _can_start_auto_watch(result):
            result["watcher"] = service.start_watcher(wait_ready=False)
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

    @mcp.tool()
    def auto_index_watcher_status() -> dict[str, Any]:
        """Return filesystem-event auto-refresh status."""
        return service.watcher_status()


def _can_start_auto_watch(result: dict[str, Any]) -> bool:
    return result.get("status") != "build-lock-timeout" and result.get("updated_at") is not None
