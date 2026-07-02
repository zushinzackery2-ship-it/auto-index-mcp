from __future__ import annotations

from typing import Any

from .pagination import PageRequest
from .path_filters import filter_indexed_files
from .service_state import ServiceBase
from ..search.backend import search_text
from ..workspace.context import ContextLoader


class ServiceSearchMixin(ServiceBase):
    def text_search(
        self,
        pattern: str,
        case_sensitive: bool = True,
        regex: bool = False,
        limit: int = 80,
        file_pattern: str | None = None,
        context_lines: int = 0,
        exclude_paths: list[str] | None = None,
        active_only: bool = False,
    ) -> dict[str, Any]:
        self._require_ready()
        if self.root_path is None:
            raise RuntimeError("auto-index root is not configured")
        if not pattern:
            raise ValueError("pattern is required")
        page = PageRequest.from_cursor(None, limit)
        view = self.view
        targets = filter_indexed_files(view.search_targets(), exclude_paths, active_only)
        backend, matches = search_text(
            self.root_path,
            targets,
            pattern,
            case_sensitive,
            regex,
            page.limit,
            file_pattern,
        )
        if context_lines > 0:
            matches = ContextLoader(view, self.root_path).attach(matches, context_lines)
        return self._with_index_status(
            {"format": "auto_index_text_search_indexed", "backend": backend, "items": matches}
        )

    def symbol_search(self, text: str = "", kind: str = "", limit: int = 80, cursor: str | None = None) -> dict[str, Any]:
        self._require_store()
        page = PageRequest.from_cursor(cursor, limit)
        rows = self.view.query_symbols(text, kind, page.fetch_limit, page.offset)
        next_cursor = page.next_cursor if len(rows) > page.limit else None
        return self._with_index_status(
            {"format": "auto_index_symbol_search_indexed", "items": rows[:page.limit], "cursor": next_cursor}
        )

    def symbol_body(self, path: str, symbol_name: str) -> dict[str, Any]:
        self._require_ready()
        if self.root_path is None:
            raise RuntimeError("auto-index root is not configured")
        if not path or not symbol_name:
            raise ValueError("path and symbol_name are required")
        lookup = self.view.get_file(path)
        if lookup.item is None:
            not_ready = self._not_ready_response()
            if not_ready is not None:
                return not_ready
            raise KeyError(f"indexed file not found: {path}")
        matches = [symbol for symbol in lookup.item["symbols"] if symbol["name"] == symbol_name]
        if not matches:
            not_ready = self._not_ready_response()
            if not_ready is not None:
                return not_ready
            raise KeyError(f"symbol not found: {symbol_name}")
        if len(matches) > 1:
            return self._with_index_status(
                {"format": "auto_index_symbol_body_ambiguous", "candidates": matches}
            )
        symbol = matches[0]
        lines = self.view.read_indexed_text(self.root_path, lookup.item).splitlines()
        start = max(1, symbol["line"])
        end = min(len(lines), symbol["end_line"])
        code = "\n".join(lines[start - 1:end])
        return self._with_index_status(
            {"format": "auto_index_symbol_body_full", "symbol": symbol, "path": path, "code": code}
        )
