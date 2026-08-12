from __future__ import annotations

import argparse
import atexit
import os
import signal
from types import FrameType

from mcp.server.fastmcp import FastMCP

from ..core.config import DEFAULT_ENABLE_REBUILD_WAIT_SECONDS
from ..core.service import AutoIndexService
from .bootstrap import PROJECT_PATH_ENV
from .lifecycle import register_lifecycle_tools, start_or_defer_auto_watch
from .navigation import register_navigation_tools
from .quality import register_quality_tools
from .search import register_search_tools
from .semantic import register_semantic_tools

SERVER_INSTRUCTIONS = """\
Persistent code index for the active project (SQLite-backed, survives
restarts, auto-refreshes on file changes).

Workflow: tools auto-attach to the workspace root on first use; call
auto_index_enable(root_path=...) only when detection fails or you need a
different root. All file paths are project-relative with forward slashes
(inputs are normalized, so absolute/backslash paths usually still resolve).

Strengths versus plain grep/read: auto_index_symbol_search finds ranked
definitions, auto_index_symbol_body returns one function's source without
reading the whole file, auto_index_symbol_refs answers "who calls this",
and auto_index_semantic_search matches natural-language descriptions
(English queries work best with the bundled model).

Errors come back as {error, hint, candidates?} - follow the hint instead of
retrying blindly. If a response says the index is still building, check
auto_index_status() and retry shortly.
"""

mcp = FastMCP("AutoIndexMCP", instructions=SERVER_INSTRUCTIONS)
_service = AutoIndexService()
_shutdown_hooks_registered = False

register_lifecycle_tools(mcp, _service)
register_navigation_tools(mcp, _service)
register_search_tools(mcp, _service)
register_quality_tools(mcp, _service)
register_semantic_tools(mcp, _service)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Auto Index MCP server")
    parser.add_argument("--project-path", default=None)
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--no-rebuild", action="store_true")
    parser.add_argument("--no-watch", action="store_true")
    parser.add_argument("--transport", choices=["stdio", "sse", "streamable-http"], default="stdio")
    parser.add_argument("--port", type=int, default=8000)
    return parser.parse_args(argv)


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


def main(argv: list[str] | None = None) -> None:
    _register_shutdown_hooks()
    args = _parse_args(argv)
    project_path = args.project_path or os.environ.get(PROJECT_PATH_ENV, "").strip() or None
    try:
        if project_path:
            result = _service.enable_reusing_index(
                project_path,
                rebuild=args.rebuild and not args.no_rebuild,
                wait_seconds=DEFAULT_ENABLE_REBUILD_WAIT_SECONDS,
            )
            if not args.no_watch:
                start_or_defer_auto_watch(_service, result)
        if args.transport != "stdio":
            mcp.settings.port = args.port
        mcp.run(transport=args.transport)
    finally:
        _shutdown_service()


if __name__ == "__main__":
    main()
