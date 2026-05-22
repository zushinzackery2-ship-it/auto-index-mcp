from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from ..compatibility.code_index import CompatService
from ..core.service import AutoIndexService


def register_compat_tools(mcp: FastMCP, service: AutoIndexService, compat: CompatService) -> None:
    @mcp.tool()
    def set_project_path(path: str) -> str:
        """Compatibility tool: initialize indexing for a project directory."""
        result = compat.set_project_path(path)
        service.start_watcher()
        return f"{result} Auto-refresh is running."

    @mcp.tool()
    def refresh_index() -> str:
        """Compatibility tool: rebuild the project index."""
        return compat.refresh_index()

    @mcp.tool()
    def build_deep_index(max_workers: int | None = None, timeout: int | None = None) -> str:
        """Compatibility tool: rebuild the full symbol index."""
        return compat.build_deep_index(max_workers, timeout)

    @mcp.tool()
    def find_files(pattern: str) -> list[str]:
        """Compatibility tool: find indexed files by glob or filename."""
        return compat.find_files(pattern)

    @mcp.tool()
    def get_file_summary(file_path: str) -> dict[str, Any]:
        """Compatibility tool: summarize one indexed file."""
        return compat.get_file_summary(file_path)

    @mcp.tool()
    def get_symbol_body(file_path: str, symbol_name: str) -> dict[str, Any]:
        """Compatibility tool: return one symbol source body."""
        return compat.get_symbol_body(file_path, symbol_name)

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
        return compat.search_code_advanced(
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
        return compat.get_settings_info()

    @mcp.tool()
    def get_file_watcher_status() -> dict[str, Any]:
        """Compatibility tool: return watcher status."""
        return compat.get_file_watcher_status()

    @mcp.tool()
    def configure_file_watcher(
        enabled: bool | None = None,
        debounce_seconds: float | None = None,
        additional_exclude_patterns: list | None = None,
        observer_type: str | None = None,
    ) -> str:
        """Compatibility tool: configure filesystem-event watcher."""
        return compat.configure_file_watcher(enabled, debounce_seconds, additional_exclude_patterns, observer_type)

    @mcp.tool()
    def clear_settings() -> str:
        """Compatibility tool: clear index settings and cache."""
        return compat.clear_settings()

    @mcp.tool()
    def create_temp_directory() -> dict[str, Any]:
        """Compatibility tool: ensure the index directory exists."""
        if service.index_root is None:
            return {"status": "not_configured", "path": None}
        service.index_root.mkdir(parents=True, exist_ok=True)
        return {"status": "success", "path": str(service.index_root)}

    @mcp.tool()
    def check_temp_directory() -> dict[str, Any]:
        """Compatibility tool: report index directory status."""
        if service.index_root is None:
            return {"exists": False, "path": None}
        return {"exists": service.index_root.exists(), "path": str(service.index_root)}

    @mcp.tool()
    def refresh_search_tools() -> str:
        """Compatibility tool: search backend is detected per request."""
        return "Search tools are detected per request."

