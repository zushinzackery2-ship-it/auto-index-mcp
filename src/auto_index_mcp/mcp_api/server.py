from __future__ import annotations

import argparse

from mcp.server.fastmcp import FastMCP

from ..compatibility.code_index import CompatService
from ..core.service import AutoIndexService
from .compat import register_compat_tools
from .lifecycle import register_lifecycle_tools
from .navigation import register_navigation_tools
from .search import register_search_tools


mcp = FastMCP("AutoIndexMCP")
_service = AutoIndexService()
_compat = CompatService(_service)

register_lifecycle_tools(mcp, _service)
register_navigation_tools(mcp, _service)
register_search_tools(mcp, _service)
register_compat_tools(mcp, _service, _compat)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Auto Index MCP server")
    parser.add_argument("--project-path", default=None)
    parser.add_argument("--no-rebuild", action="store_true")
    parser.add_argument("--no-watch", action="store_true")
    parser.add_argument("--transport", choices=["stdio", "sse", "streamable-http"], default="stdio")
    parser.add_argument("--port", type=int, default=8000)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    if args.project_path:
        _service.enable(args.project_path, rebuild=not args.no_rebuild)
        if not args.no_watch:
            _service.start_watcher()
    if args.transport != "stdio":
        mcp.settings.port = args.port
    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()

