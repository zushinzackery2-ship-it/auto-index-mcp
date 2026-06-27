from __future__ import annotations

import argparse
import atexit
import signal
from types import FrameType

from mcp.server.fastmcp import FastMCP

from ..core.service import AutoIndexService
from .lifecycle import register_lifecycle_tools
from .navigation import register_navigation_tools
from .quality import register_quality_tools
from .search import register_search_tools
from .semantic import register_semantic_tools


mcp = FastMCP("AutoIndexMCP")
_service = AutoIndexService()
_shutdown_hooks_registered = False

register_lifecycle_tools(mcp, _service)
register_navigation_tools(mcp, _service)
register_search_tools(mcp, _service)
register_quality_tools(mcp, _service)
register_semantic_tools(mcp, _service)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Auto Index MCP server")
    parser.add_argument("--project-path", default=None)
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--no-rebuild", action="store_true")
    parser.add_argument("--no-watch", action="store_true")
    parser.add_argument("--transport", choices=["stdio", "sse", "streamable-http"], default="stdio")
    parser.add_argument("--port", type=int, default=8000)
    return parser.parse_args()


def _shutdown_service() -> None:
    _service.stop_watcher()


def _handle_shutdown_signal(signum: int, frame: FrameType | None) -> None:
    _ = frame
    _shutdown_service()
    raise SystemExit(128 + signum)


def _register_shutdown_hooks() -> None:
    global _shutdown_hooks_registered
    if _shutdown_hooks_registered:
        return
    atexit.register(_shutdown_service)
    for signal_name in ("SIGINT", "SIGTERM"):
        signum = getattr(signal, signal_name, None)
        if signum is not None:
            signal.signal(signum, _handle_shutdown_signal)
    _shutdown_hooks_registered = True


def main() -> None:
    _register_shutdown_hooks()
    args = _parse_args()
    try:
        if args.project_path:
            result = _service.enable_reusing_index(args.project_path, rebuild=args.rebuild and not args.no_rebuild)
            if not args.no_watch:
                if _service.can_start_auto_watch(result):
                    _service.start_watcher(wait_ready=False)
                elif result.get("status") == "indexing-in-background":
                    _service.request_auto_watch_after_build()
        if args.transport != "stdio":
            mcp.settings.port = args.port
        mcp.run(transport=args.transport)
    finally:
        _shutdown_service()


if __name__ == "__main__":
    main()
