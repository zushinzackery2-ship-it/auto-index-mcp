from __future__ import annotations

from typing import Any, Literal, Optional

from mcp.server.fastmcp import Context, FastMCP

from ..core.background_indexer import BackgroundIndexer
from ..core.config import DEFAULT_ENABLE_REBUILD_WAIT_SECONDS, DEFAULT_WATCH_DEBOUNCE_SECONDS
from ..core.service import AutoIndexService
from ..core.tool_errors import invalid_argument, not_enabled
from .bootstrap import ensure_enabled, resolve_project_root
from .guard import run_tool

ManageAction = Literal[
    "rebuild",
    "clear",
    "watch_start",
    "watch_stop",
    "disable",
    "diff",
    "ignore_status",
    "ignore_add",
    "ignore_replace",
    "ignore_clear",
]

# Actions that operate on an active index and may lazily auto-enable one.
_ACTIONS_NEEDING_INDEX = {
    "rebuild",
    "clear",
    "diff",
    "watch_start",
    "ignore_status",
    "ignore_add",
    "ignore_replace",
    "ignore_clear",
}


def register_lifecycle_tools(mcp: FastMCP, service: AutoIndexService) -> None:
    @mcp.tool()
    async def auto_index_enable(
        root_path: str = "",
        rebuild: bool = False,
        auto_watch: bool = True,
        ctx: Optional[Context] = None,
    ) -> dict[str, Any]:
        """Attach the index to a project (usually the one call to make first).

        ``root_path`` is the absolute project directory; leave it empty to
        auto-detect from AUTO_INDEX_PROJECT_PATH or the MCP client's workspace
        roots. Reuses a persisted index when fresh (fast), otherwise builds in
        the background - poll auto_index_status(). Set rebuild=true only to
        force a full rescan.
        """
        root = root_path.strip() or None
        if root is None:
            detected = await resolve_project_root(service, ctx)
            if detected is None:
                return not_enabled(
                    "root_path was empty and no project root could be auto-detected"
                )
            root = str(detected)

        def _enable() -> dict[str, Any]:
            result = service.enable_reusing_index(
                root,
                rebuild,
                wait_seconds=DEFAULT_ENABLE_REBUILD_WAIT_SECONDS,
            )
            if auto_watch:
                start_or_defer_auto_watch(service, result)
            return result

        return run_tool(_enable)

    @mcp.tool()
    def auto_index_status() -> dict[str, Any]:
        """Index health in one compact payload: root, file/vector counts,
        watcher state, and build progress. Call when results look stale or a
        tool reported the index was still building."""
        return run_tool(service.status)

    @mcp.tool()
    async def auto_index_manage(
        action: ManageAction,
        patterns: list[str] | None = None,
        target: Literal["ignore", "privileged"] = "ignore",
        delete_file: bool = False,
        debounce_seconds: float = DEFAULT_WATCH_DEBOUNCE_SECONDS,
        ctx: Optional[Context] = None,
    ) -> dict[str, Any]:
        """Maintenance operations, rarely needed in normal navigation.

        action: "rebuild" full rescan in background | "clear" wipe indexed
        data (delete_file=true also removes the db) | "watch_start" /
        "watch_stop" filesystem auto-refresh | "disable" detach |
        "diff" index-vs-filesystem drift | "ignore_status" / "ignore_add" /
        "ignore_replace" / "ignore_clear" gitignore-style runtime patterns
        (``patterns`` list; target="privileged" edits the oversized-file
        allow-list instead).
        """
        if action in _ACTIONS_NEEDING_INDEX:
            blocked = await ensure_enabled(service, ctx)
            if blocked is not None:
                return blocked
        if action == "rebuild":
            return run_tool(service.rebuild)
        if action == "clear":
            return run_tool(service.clear, delete_file)
        if action == "watch_start":
            return run_tool(service.start_watcher, debounce_seconds)
        if action == "watch_stop":
            return run_tool(service.stop_watcher)
        if action == "disable":
            return run_tool(service.disable)
        if action == "diff":
            return run_tool(service.diff_filesystem)
        if action.startswith("ignore_"):
            mode = action.removeprefix("ignore_")
            return run_tool(service.configure_ignore, patterns, mode, target)
        return invalid_argument(f"unknown action: {action}")


def start_or_defer_auto_watch(
    service: AutoIndexService,
    result: dict[str, Any],
) -> dict[str, Any]:
    if service.can_start_auto_watch(result):
        result["watcher"] = service.start_watcher(wait_ready=False)
        return result
    if result.get("status") not in ("indexing-in-background", "already-running"):
        return result
    service.request_auto_watch_after_build()
    background = service.background
    if background is None or background.is_running():
        return result
    ready = _background_last_result(background)
    service.cancel_auto_watch_after_build()
    if service.can_start_auto_watch(ready):
        result["watcher"] = service.start_watcher(wait_ready=False)
    return result


def _background_last_result(background: BackgroundIndexer) -> dict[str, Any] | None:
    last_result = background.status().get("last_result")
    return last_result if isinstance(last_result, dict) else None
