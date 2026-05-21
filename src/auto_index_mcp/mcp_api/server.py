from __future__ import annotations

import argparse
from typing import Any

from mcp.server.fastmcp import FastMCP

from ..core.service import AutoIndexService
from ..compatibility.code_index import CompatService

mcp = FastMCP("AutoIndexMCP")
_service = AutoIndexService()
_compat = CompatService(_service)


@mcp.resource("files://{file_path}")
def get_file_content(file_path: str) -> str:
    """Return the content of a project file."""
    return _service.file_content(file_path)


@mcp.tool()
def auto_index_enable(root_path: str, rebuild: bool = True) -> dict[str, Any]:
    """Enable persistent code auto-indexing and optionally rebuild immediately."""
    return _service.enable(root_path, rebuild)


@mcp.tool()
def auto_index_disable() -> dict[str, Any]:
    """Disable auto-index state while keeping the persisted SQLite index."""
    return _service.disable()


@mcp.tool()
def auto_index_status() -> dict[str, Any]:
    """Return index status, freshness, counts, and index location."""
    return _service.status()


@mcp.tool()
def auto_index_rebuild() -> dict[str, Any]:
    """Force a full source tree rebuild into the persisted index."""
    return _service.rebuild()


@mcp.tool()
def auto_index_flush() -> dict[str, Any]:
    """Flush current state. SQLite commits are immediate, so this reports durability."""
    return _service.flush()


@mcp.tool()
def auto_index_clear(delete_file: bool = False) -> dict[str, Any]:
    """Clear indexed data and optionally delete the SQLite file."""
    return _service.clear(delete_file)


@mcp.tool()
def auto_index_watcher_start(interval_seconds: float = 2.0) -> dict[str, Any]:
    """Start polling auto-refresh for the active root."""
    return _service.start_watcher(interval_seconds)


@mcp.tool()
def auto_index_watcher_stop() -> dict[str, Any]:
    """Stop polling auto-refresh."""
    return _service.stop_watcher()


@mcp.tool()
def auto_index_watcher_status() -> dict[str, Any]:
    """Return polling auto-refresh status."""
    return _service.watcher_status()


@mcp.tool()
def auto_index_overview(limit: int = 30) -> dict[str, Any]:
    """Return a compact codebase overview for first-pass context gathering."""
    return _service.overview(limit)


@mcp.tool()
def auto_index_tree_get(root_path: str = "", depth: int = 2, limit: int = 120) -> dict[str, Any]:
    """Return a compact folder tree with file counts, language mix, and samples."""
    return _service.tree_get(root_path, depth, limit)


@mcp.tool()
def auto_index_query(
    text: str = "",
    languages: list[str] | None = None,
    parent: str = "",
    limit: int = 80,
    cursor: str | None = None,
) -> dict[str, Any]:
    """Query indexed files by text, language, parent path, and cursor."""
    return _service.query(text, languages, parent, limit, cursor)


@mcp.tool()
def auto_index_text_search(
    pattern: str,
    case_sensitive: bool = True,
    regex: bool = False,
    limit: int = 80,
    file_pattern: str | None = None,
) -> dict[str, Any]:
    """Search indexed source text and return compact path/line matches."""
    return _service.text_search(pattern, case_sensitive, regex, limit, file_pattern)


@mcp.tool()
def auto_index_symbol_search(
    text: str = "",
    kind: str = "",
    limit: int = 80,
    cursor: str | None = None,
) -> dict[str, Any]:
    """Search indexed symbols by name, signature, and optional symbol kind."""
    return _service.symbol_search(text, kind, limit, cursor)


@mcp.tool()
def auto_index_symbol_body(path: str, symbol_name: str) -> dict[str, Any]:
    """Return the source body of a symbol from an indexed file."""
    return _service.symbol_body(path, symbol_name)


@mcp.tool()
def auto_index_file_summary(path: str) -> dict[str, Any]:
    """Return imports, symbols, and lightweight complexity for one indexed file."""
    return _service.file_summary(path)


@mcp.tool()
def auto_index_get(path: str) -> dict[str, Any]:
    """Return one indexed file record."""
    return _service.get(path)


@mcp.tool()
def auto_index_resolve_path(path: str, limit: int = 20) -> dict[str, Any]:
    """Resolve a fuzzy file name or path into indexed candidates."""
    return _service.resolve_path(path, limit)


@mcp.tool()
def auto_index_diff_filesystem() -> dict[str, Any]:
    """Compare the persisted index with the current filesystem."""
    return _service.diff_filesystem()


@mcp.tool()
def set_project_path(path: str) -> str:
    """Compatibility tool: initialize indexing for a project directory."""
    return _compat.set_project_path(path)


@mcp.tool()
def refresh_index() -> str:
    """Compatibility tool: rebuild the project index."""
    return _compat.refresh_index()


@mcp.tool()
def build_deep_index(max_workers: int | None = None, timeout: int | None = None) -> str:
    """Compatibility tool: rebuild the full symbol index."""
    return _compat.build_deep_index(max_workers, timeout)


@mcp.tool()
def find_files(pattern: str) -> list[str]:
    """Compatibility tool: find indexed files by glob or filename."""
    return _compat.find_files(pattern)


@mcp.tool()
def get_file_summary(file_path: str) -> dict[str, Any]:
    """Compatibility tool: summarize one indexed file."""
    return _compat.get_file_summary(file_path)


@mcp.tool()
def get_symbol_body(file_path: str, symbol_name: str) -> dict[str, Any]:
    """Compatibility tool: return one symbol source body."""
    return _compat.get_symbol_body(file_path, symbol_name)


@mcp.tool()
def search_code_advanced(
    pattern: str,
    case_sensitive: bool = True,
    context_lines: int = 0,
    file_pattern: str | None = None,
    fuzzy: bool = False,
    regex: bool | None = None,
    start_index: int = 0,
    max_results: int | None = 10,
) -> dict[str, Any]:
    """Compatibility tool: search code with pagination and optional filtering."""
    return _compat.search_code_advanced(
        pattern,
        case_sensitive,
        context_lines,
        file_pattern,
        fuzzy,
        regex,
        start_index,
        max_results,
    )


@mcp.tool()
def get_settings_info() -> dict[str, Any]:
    """Compatibility tool: return project/index settings."""
    return _compat.get_settings_info()


@mcp.tool()
def get_file_watcher_status() -> dict[str, Any]:
    """Compatibility tool: return watcher status."""
    return _compat.get_file_watcher_status()


@mcp.tool()
def configure_file_watcher(
    enabled: bool | None = None,
    debounce_seconds: float | None = None,
    additional_exclude_patterns: list | None = None,
    observer_type: str | None = None,
) -> str:
    """Compatibility tool: configure polling watcher."""
    return _compat.configure_file_watcher(enabled, debounce_seconds, additional_exclude_patterns, observer_type)


@mcp.tool()
def clear_settings() -> str:
    """Compatibility tool: clear index settings and cache."""
    return _compat.clear_settings()


@mcp.tool()
def create_temp_directory() -> dict[str, Any]:
    """Compatibility tool: ensure the index directory exists."""
    if _service.index_root is None:
        return {"status": "not_configured", "path": None}
    _service.index_root.mkdir(parents=True, exist_ok=True)
    return {"status": "success", "path": str(_service.index_root)}


@mcp.tool()
def check_temp_directory() -> dict[str, Any]:
    """Compatibility tool: report index directory status."""
    if _service.index_root is None:
        return {"exists": False, "path": None}
    return {"exists": _service.index_root.exists(), "path": str(_service.index_root)}


@mcp.tool()
def refresh_search_tools() -> str:
    """Compatibility tool: search backend is detected per request."""
    return "Search tools are detected per request."


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Auto Index MCP server")
    parser.add_argument("--project-path", default=None)
    parser.add_argument("--no-rebuild", action="store_true")
    parser.add_argument("--transport", choices=["stdio", "sse", "streamable-http"], default="stdio")
    parser.add_argument("--port", type=int, default=8000)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    if args.project_path:
        _service.enable(args.project_path, rebuild=not args.no_rebuild)
    if args.transport != "stdio":
        mcp.settings.port = args.port
    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
