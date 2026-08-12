from __future__ import annotations

from pathlib import Path
from typing import Any

from .navigation_format import compact_file, compact_symbol, overview_result, tree_result
from .pagination import PageRequest
from .path_filters import is_glob_pattern
from .path_normalize import normalize_input_path
from .service_state import ServiceBase
from .tool_errors import file_not_found


class ServiceNavigationMixin(ServiceBase):
    def overview(self, limit: int = 20) -> dict[str, Any]:
        self._store_context()
        files = self.view.all_files()
        return self._with_index_status(overview_result(files, limit))

    def tree_get(self, dir: str = "", depth: int = 2, limit: int = 50) -> dict[str, Any]:
        self._store_context()
        dir_path = normalize_input_path(dir, self.root_path) if dir else ""
        status = self._index_status()
        if status is not None and not status["ready"]:
            partial = self.tree_progress.snapshot(dir_path, depth, limit)
            if partial is not None:
                partial["index_status"] = _partial_tree_status(status, partial)
                return partial
        files = self.view.all_files()
        return self._with_index_status(tree_result(files, dir_path, depth, limit))

    def find_files(
        self,
        query: str = "",
        dir: str = "",
        languages: list[str] | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """Fuzzy file finder: name/path substring, glob, or plain browse.

        Merges the old ``query`` (filtered listing) and ``resolve_path``
        (fuzzy name resolution) surfaces into one tool-facing entry point.
        """
        self._store_context()
        page = PageRequest.from_cursor(cursor, limit)
        needle = normalize_input_path(query, self.root_path).lower() if query else ""
        dir_prefix = normalize_input_path(dir, self.root_path).lower().rstrip("/") if dir else ""
        wanted_languages = {value.lower() for value in (languages or [])}
        glob = bool(needle) and is_glob_pattern(needle)

        matched: list[str] = []
        seen: set[str] = set()

        def consider(header: dict[str, Any]) -> None:
            file_path = header["path"]
            if file_path in seen:
                return
            candidate = file_path.lower()
            if dir_prefix and not (candidate == dir_prefix or candidate.startswith(dir_prefix + "/")):
                return
            if wanted_languages and str(header.get("language", "")).lower() not in wanted_languages:
                return
            if needle:
                name = header["name"].lower()
                if glob:
                    if not (Path(candidate).match(needle) or Path(candidate).name.lower() == needle):
                        return
                elif not (needle in candidate or name == needle):
                    return
            seen.add(file_path)
            matched.append(file_path)

        for header in self.view.file_headers():
            consider(header)
        if needle and not glob and not matched:
            # No path hit at all: fall back to an exact symbol-name lookup so
            # "where does X live" still resolves through the symbols table.
            for row in self.view.query_symbols(query, "", 200, 0):
                if row["name"].lower() == needle and row["file_path"] not in seen:
                    seen.add(row["file_path"])
                    matched.append(row["file_path"])

        window = matched[page.offset:page.offset + page.limit]
        items = []
        for file_path in window:
            lookup = self.view.get_file(file_path)
            if lookup.item is not None:
                items.append(compact_file(lookup.item))
        next_cursor = page.next_cursor if len(matched) > page.offset + page.limit else None
        return self._with_index_status({
            "format": "auto_index_files_v2",
            "items": items,
            "total_matches": len(matched),
            "cursor": next_cursor,
        })

    def file_summary(self, path: str) -> dict[str, Any]:
        self._store_context()
        resolved, lookup = self._lookup_indexed_file(path)
        if lookup.item is None:
            not_ready = self._not_ready_response()
            if not_ready is not None:
                return not_ready
            return file_not_found(path, self._path_candidates(path))
        symbols = [compact_symbol(symbol) for symbol in lookup.item["symbols"]]
        complexities = [symbol.get("complexity", 1) for symbol in lookup.item["symbols"]]
        return self._with_index_status({
            "format": "auto_index_file_summary_v2",
            "path": lookup.item["path"],
            "language": lookup.item["language"],
            "line_count": lookup.item["line_count"],
            "imports": lookup.item["imports"],
            "symbol_count": len(symbols),
            "symbols": symbols,
            "total_complexity": sum(complexities),
            "max_complexity": max(complexities, default=0),
        })

    def get(self, path: str) -> dict[str, Any]:
        self._store_context()
        resolved, lookup = self._lookup_indexed_file(path)
        if lookup.item is None:
            not_ready = self._not_ready_response()
            if not_ready is not None:
                return not_ready
            return file_not_found(path, self._path_candidates(path))
        return self._with_index_status({"format": "auto_index_get_full", "item": lookup.item})

    def file_content(self, path: str) -> str:
        root, _store = self._ready_context()
        return self.view.read_text(root, normalize_input_path(path, root))

    def diff_filesystem(self) -> dict[str, Any]:
        root, _store = self._ready_context()
        diff = self.view.diff_filesystem(root)
        added = diff["added"]
        deleted = diff["deleted"]
        changed = diff["changed"]
        return self._with_index_status({
            "format": "auto_index_diff_v2",
            "added": added[:100],
            "deleted": deleted[:100],
            "changed": changed[:100],
            "added_count": len(added),
            "deleted_count": len(deleted),
            "changed_count": len(changed),
        })

    def all_files(self) -> list[dict[str, Any]]:
        self._store_context()
        return self.view.all_files()


def _partial_tree_status(status: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    merged = dict(status)
    merged["partial"] = True
    merged["tree"] = {
        "requested_depth": result["requested_depth"],
        "completed_depth": result["completed_depth"],
    }
    return merged
