from __future__ import annotations

from ..core.service import AutoIndexService


def register_lsp_tools(mcp, service: AutoIndexService) -> None:
    @mcp.tool()
    def auto_index_lsp_start(timeout_seconds: float = 10.0) -> str:
        """Start available LSP servers for the active indexed project."""
        return service.start_lsp(timeout_seconds, background=True)

    @mcp.tool()
    def auto_index_lsp_check(path: str | None = None, limit: int = 80, timeout_seconds: float = 5.0) -> str:
        """Pull LSP diagnostics for the active indexed project or one file."""
        return service.check_lsp(path, limit, timeout_seconds)

    @mcp.tool()
    def auto_index_lsp_shutdown(timeout_seconds: float = 5.0) -> str:
        """Shutdown all LSP servers for the active indexed project."""
        return service.stop_lsp(timeout_seconds)
