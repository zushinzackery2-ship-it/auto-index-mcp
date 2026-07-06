from __future__ import annotations

from typing import Any

from .pagination import PageRequest
from .navigation_format import presentable_symbol
from .path_filters import filter_indexed_files
from .service_state import ServiceBase
from .subtoken import split_identifier
from ..indexing.symbol_query import (
    RANK_EXACT_NAME,
    RANK_NAME_PREFIX,
    RANK_NAME_SUBSTRING,
    RANK_SIGNATURE,
)
from ..search.backend import search_text
from ..workspace.context import ContextLoader

_MATCH_LABELS = {
    RANK_EXACT_NAME: "exact-name",
    RANK_NAME_PREFIX: "name-prefix",
    RANK_NAME_SUBSTRING: "name-substring",
    RANK_SIGNATURE: "signature",
}


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
        match_mode = "ranked" if text else "all"
        if text and not rows and self._direct_symbols_empty(text, kind, page.offset):
            # Progressive relaxation: no direct hit anywhere, so retry with
            # subtoken AND-matching (camelCase/snake_case aware). Only queries
            # that actually split into several subtokens gain anything here.
            subtokens = split_identifier(text)
            if len(subtokens) >= 2:
                rows = self.view.query_symbols_relaxed(subtokens, kind, page.fetch_limit, page.offset)
                if rows:
                    match_mode = "subtoken-relaxed"
        next_cursor = page.next_cursor if len(rows) > page.limit else None
        items = [_present_symbol(row) for row in rows[:page.limit]]
        return self._with_index_status(
            {
                "format": "auto_index_symbol_search_indexed",
                "match_mode": match_mode,
                "items": items,
                "cursor": next_cursor,
            }
        )

    def _direct_symbols_empty(self, text: str, kind: str, offset: int) -> bool:
        # Page one being empty proves the direct query has no hits; deeper
        # pages must re-probe page one so relaxation never fires merely
        # because pagination walked past the end of real direct results.
        if offset == 0:
            return True
        return not self.view.query_symbols(text, kind, 1, 0)

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
                {
                    "format": "auto_index_symbol_body_ambiguous",
                    "candidates": [presentable_symbol(match) for match in matches],
                }
            )
        symbol = matches[0]
        lines = self.view.read_indexed_text(self.root_path, lookup.item).splitlines()
        start = max(1, symbol["line"])
        end = min(len(lines), symbol["end_line"])
        code = "\n".join(lines[start - 1:end])
        return self._with_index_status(
            {
                "format": "auto_index_symbol_body_full",
                "symbol": presentable_symbol(symbol),
                "path": path,
                "code": code,
            }
        )


def _present_symbol(row: dict[str, Any]) -> dict[str, Any]:
    """Replace the internal match_rank tier with a human-readable label."""
    shaped = presentable_symbol(row)
    rank = shaped.pop("match_rank", None)
    if rank is not None:
        shaped["match"] = _MATCH_LABELS.get(int(rank), "subtoken")
    return shaped
