from __future__ import annotations

from pathlib import Path
from typing import Any

from .navigation_format import compact_file, overview_result, presentable_symbol, tree_result
from .pagination import PageRequest
from .path_filters import is_glob_pattern
from .service_state import ServiceBase


class ServiceNavigationMixin(ServiceBase):
    def overview(self, limit: int = 30) -> dict[str, Any]:
        self._store_context()
        files = self.view.all_files()
        return self._with_index_status(overview_result(files, limit))

    def tree_get(self, root_path: str = "", depth: int = 2, limit: int = 120) -> dict[str, Any]:
        self._store_context()
        status = self._index_status()
        if status is not None and not status["ready"]:
            partial = self.tree_progress.snapshot(root_path, depth, limit)
            if partial is not None:
                partial["index_status"] = _partial_tree_status(status, partial)
                return partial
        files = self.view.all_files()
        return self._with_index_status(tree_result(files, root_path, depth, limit))

    def query(
        self,
        text: str = "",
        languages: list[str] | None = None,
        parent: str = "",
        limit: int = 80,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        self._store_context()
        page = PageRequest.from_cursor(cursor, limit)
        rows = self.view.query(text, languages or [], parent, page.fetch_limit, page.offset)
        next_cursor = page.next_cursor if len(rows) > page.limit else None
        return self._with_index_status({
            "format": "auto_index_query_indexed",
            "items": [compact_file(row) for row in rows[:page.limit]],
            "cursor": next_cursor,
        })

    def file_summary(self, path: str) -> dict[str, Any]:
        self._store_context()
        lookup = self.view.get_file(path)
        if lookup.item is None:
            not_ready = self._not_ready_response()
            if not_ready is not None:
                return not_ready
            raise KeyError(f"indexed file not found: {path}")
        symbols = [presentable_symbol(symbol) for symbol in lookup.item["symbols"]]
        return self._with_index_status({
            "format": "auto_index_file_summary_full",
            "path": lookup.item["path"],
            "language": lookup.item["language"],
            "line_count": lookup.item["line_count"],
            "imports": lookup.item["imports"],
            "symbol_count": len(symbols),
            "symbols": symbols,
            "total_complexity": sum(symbol.get("complexity", 1) for symbol in symbols),
            "max_complexity": max((symbol.get("complexity", 1) for symbol in symbols), default=0),
        })

    def get(self, path: str) -> dict[str, Any]:
        self._store_context()
        lookup = self.view.get_file(path)
        if lookup.item is None:
            not_ready = self._not_ready_response()
            if not_ready is not None:
                return not_ready
            raise KeyError(f"indexed file not found: {path}")
        return self._with_index_status({"format": "auto_index_get_full", "item": lookup.item})

    def file_content(self, path: str) -> str:
        root, _store = self._ready_context()
        return self.view.read_text(root, path)

    def resolve_path(self, path: str, limit: int = 20) -> dict[str, Any]:
        self._store_context()
        needle = path.lower().replace("\\", "/")
        matches: list[dict[str, Any]] = []
        seen_paths: set[str] = set()

        def try_add(file_path: str) -> bool:
            """Append the full record for one path; True once limit is reached."""
            if file_path not in seen_paths:
                seen_paths.add(file_path)
                lookup = self.view.get_file(file_path)
                if lookup.item is not None:
                    matches.append(compact_file(lookup.item))
            return len(matches) >= limit

        # Path/name matching walks light headers only; the symbol JSON of the
        # whole workspace is never deserialized. Full records are point-fetched
        # for the (at most ``limit``) hits.
        glob = is_glob_pattern(needle)
        for header in self.view.file_headers():
            candidate = header["path"].lower()
            if glob:
                if not (Path(candidate).match(needle) or Path(candidate).name.lower() == needle):
                    continue
            elif not (candidate == needle or header["name"].lower() == needle or needle in candidate):
                continue
            if try_add(header["path"]):
                break
        if not glob and len(matches) < limit:
            # Exact symbol-name lookup goes through the indexed symbols table
            # instead of scanning per-file symbol JSON.
            for row in self.view.query_symbols(path, "", 200, 0):
                if row["name"].lower() != needle:
                    continue
                if try_add(row["file_path"]):
                    break
        return self._with_index_status({"format": "auto_index_resolve_indexed", "items": matches})

    def diff_filesystem(self) -> dict[str, Any]:
        root, _store = self._ready_context()
        diff = self.view.diff_filesystem(root)
        added = diff["added"]
        deleted = diff["deleted"]
        changed = diff["changed"]
        return self._with_index_status({
            "format": "auto_index_diff_indexed",
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
