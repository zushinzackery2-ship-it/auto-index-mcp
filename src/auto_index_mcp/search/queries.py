from __future__ import annotations

from typing import Any

from ..domain.pagination import PageRequest
from .navigation_format import MAX_PRESENTED_CALLERS, compact_symbol
from ..workspace.path_filters import filter_indexed_files
from .subtoken import split_identifier
from ..domain.responses import error_response, file_not_found, symbol_not_found
from ..storage.symbols import (
    RANK_EXACT_NAME,
    RANK_NAME_PREFIX,
    RANK_NAME_SUBSTRING,
    RANK_SIGNATURE,
)
from .backend import search_text
from ..workspace.context import ContextLoader
from ..workspace.source import MAX_BODY_BYTES, read_source, source_slice

_MATCH_LABELS = {
    RANK_EXACT_NAME: "exact-name",
    RANK_NAME_PREFIX: "name-prefix",
    RANK_NAME_SUBSTRING: "name-substring",
    RANK_SIGNATURE: "signature",
}

# Same-name definitions across a project are common (methods, overloads);
# refs answers for a handful of them at once instead of forcing a path first.
MAX_REF_DEFINITIONS = 5


class SearchQueries:
    def __init__(self, project, state) -> None:
        self.project = project
        self.state = state

    def text_search(
        self,
        pattern: str,
        case_sensitive: bool = True,
        regex: bool = False,
        limit: int = 20,
        file_pattern: str | None = None,
        context_lines: int = 0,
        exclude_paths: list[str] | None = None,
        active_only: bool = False,
    ) -> dict[str, Any]:
        self.project._require_ready()
        if self.project.root_path is None:
            raise RuntimeError("auto-index root is not configured")
        if not pattern:
            raise ValueError("pattern is required")
        page = PageRequest.from_cursor(None, limit)
        view = self.project.view
        targets = filter_indexed_files(view.search_targets(), exclude_paths, active_only)
        result = search_text(
            self.project.root_path,
            targets,
            pattern,
            case_sensitive,
            regex,
            page.limit,
            file_pattern,
        )
        matches = result.matches
        if context_lines > 0:
            matches = ContextLoader(view, self.project.root_path).attach(matches, context_lines)
        response = dict(format="auto_index_text_search_v2", backend=result.backend, items=matches, complete=result.complete)
        if result.message:
            response["message"] = result.message
        if result.status != "ok":
            response = error_response(f"search-{result.status.replace('_', '-')}", result.message,
                                      hint="check the search diagnostic and ripgrep installation",
                                      items=matches, complete=False, backend=result.backend)
        return self.state._with_index_status(
            response
        )

    def symbol_search(self, text: str = "", kind: str = "", limit: int = 20, cursor: str | None = None) -> dict[str, Any]:
        self.project._require_store()
        page = PageRequest.from_cursor(cursor, limit)
        rows = self.project.view.query_symbols(text, kind, page.fetch_limit, page.offset)
        match_mode = "ranked" if text else "all"
        if text and not rows and self._direct_symbols_empty(text, kind, page.offset):
            # Progressive relaxation: no direct hit anywhere, so retry with
            # subtoken AND-matching (camelCase/snake_case aware). Only queries
            # that actually split into several subtokens gain anything here.
            subtokens = split_identifier(text)
            if len(subtokens) >= 2:
                rows = self.project.view.query_symbols_relaxed(subtokens, kind, page.fetch_limit, page.offset)
                if rows:
                    match_mode = "subtoken-relaxed"
        next_cursor = page.next_cursor if len(rows) > page.limit else None
        items = [_present_symbol(row) for row in rows[:page.limit]]
        return self.state._with_index_status(
            {
                "format": "auto_index_symbol_search_v2",
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
        return not self.project.view.query_symbols(text, kind, 1, 0)

    def symbol_body(self, symbol_name: str, path: str = "", line: int = 0) -> dict[str, Any]:
        """Source code of one symbol; ``path`` narrows same-name definitions.

        Without ``path`` the whole index is searched by exact name, so the
        common "show me function X" flow is a single call. ``line`` picks one
        of several same-name definitions inside a file.
        """
        self.project._require_ready()
        if self.project.root_path is None:
            raise RuntimeError("auto-index root is not configured")
        if not symbol_name:
            raise ValueError("symbol_name is required")
        matches, error = self._locate_symbols(symbol_name, path)
        if error is not None:
            return error
        if line:
            narrowed = [m for m in matches if m["line"] <= line <= max(m["line"], m.get("end_line", m["line"]))]
            matches = narrowed or matches
        if len(matches) > 1:
            return self.state._with_index_status(
                {
                    "format": "auto_index_symbol_body_ambiguous",
                    "candidates": [compact_symbol(match) for match in matches],
                    "hint": "several definitions share this name; pass path (and line) to pick one",
                }
            )
        symbol = matches[0]
        file_path = symbol["file_path"]
        lookup = self.project.view.get_file(file_path)
        if lookup.item is None:
            return file_not_found(file_path, self.project._path_candidates(file_path))
        lines, metadata = read_source(self.project.root_path, lookup.item, require_fresh=True)
        start = max(1, symbol["line"])
        code, end, truncated = source_slice(lines, start, symbol["end_line"], MAX_BODY_BYTES)
        return self.state._with_index_status(
            {
                "format": "auto_index_symbol_body_v2",
                "path": file_path,
                "name": symbol["name"],
                "kind": symbol.get("kind"),
                "line": start,
                "end_line": end,
                "code": code,
                "indexed_end_line": symbol["end_line"],
                "truncated": truncated,
                "byte_limit": MAX_BODY_BYTES,
                **metadata,
            }
        )

    def symbol_refs(
        self,
        symbol_name: str,
        path: str = "",
        direction: str = "both",
        limit: int = MAX_PRESENTED_CALLERS,
    ) -> dict[str, Any]:
        """Call-graph neighborhood of a symbol: who calls it, what it calls.

        This is the persistent index's find-references surface; the data was
        previously buried inside every search row.
        """
        self.project._require_ready()
        if not symbol_name:
            raise ValueError("symbol_name is required")
        if direction not in ("callers", "callees", "both"):
            raise ValueError("direction must be one of: callers, callees, both")
        matches, error = self._locate_symbols(symbol_name, path)
        if error is not None:
            return error
        safe_limit = max(1, min(int(limit), 200))
        items = []
        for symbol in matches[:MAX_REF_DEFINITIONS]:
            entry = compact_symbol(symbol)
            if direction in ("callers", "both"):
                callers = list(symbol.get("called_by") or [])
                entry["callers"] = callers[:safe_limit]
                entry["caller_count"] = len(callers)
            if direction in ("callees", "both"):
                callees = list(symbol.get("calls") or [])
                entry["callees"] = callees[:safe_limit]
                entry["callee_count"] = len(callees)
            items.append(entry)
        return self.state._with_index_status(
            {
                "format": "auto_index_symbol_refs_v2",
                "direction": direction,
                "items": items,
                "definition_count": len(matches),
            }
        )

    def _locate_symbols(
        self,
        symbol_name: str,
        path: str,
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        """Exact-name symbol rows, optionally scoped to one file.

        On a miss, returns a structured error carrying near-name candidates
        so the caller can self-correct without another round trip.
        """
        if path:
            resolved, lookup = self.project._lookup_indexed_file(path)
            if lookup.item is None:
                not_ready = self.state._not_ready_response()
                if not_ready is not None:
                    return [], not_ready
                return [], file_not_found(path, self.project._path_candidates(path))
            matches = [
                dict(symbol, file_path=resolved)
                for symbol in lookup.item["symbols"]
                if symbol["name"] == symbol_name
            ]
            if not matches:
                matches = [
                    dict(symbol, file_path=resolved)
                    for symbol in lookup.item["symbols"]
                    if symbol["name"].lower() == symbol_name.lower()
                ]
            if matches:
                return matches, None
            not_ready = self.state._not_ready_response()
            if not_ready is not None:
                return [], not_ready
            candidates = [
                compact_symbol(dict(symbol, file_path=resolved))
                for symbol in lookup.item["symbols"]
                if symbol_name.lower() in symbol["name"].lower()
            ][:5]
            return [], symbol_not_found(symbol_name, resolved, candidates)
        rows = self.project.view.query_symbols(symbol_name, "", 100, 0)
        matches = [row for row in rows if row["name"] == symbol_name]
        if not matches:
            matches = [row for row in rows if row["name"].lower() == symbol_name.lower()]
        if matches:
            return matches, None
        not_ready = self.state._not_ready_response()
        if not_ready is not None:
            return [], not_ready
        candidates = [compact_symbol(row) for row in rows[:5]]
        return [], symbol_not_found(symbol_name, "", candidates)


def _present_symbol(row: dict[str, Any]) -> dict[str, Any]:
    """Compact search row plus a human-readable match tier."""
    shaped = compact_symbol(row)
    rank = row.get("match_rank")
    if rank is not None:
        shaped["match"] = _MATCH_LABELS.get(int(rank), "subtoken")
    return shaped
