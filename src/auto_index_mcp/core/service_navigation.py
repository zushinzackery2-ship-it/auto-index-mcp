from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, cast

from .navigation_format import compact_file, overview_result, tree_result
from .pagination import PageRequest
from .path_filters import is_glob_pattern
from ..indexing.store import IndexStore
from ..workspace.view import WorkspaceView


class _NavigationService(Protocol):
    root_path: Path | None

    @property
    def view(self) -> WorkspaceView:
        ...

    def _store_context(self) -> IndexStore:
        ...

    def _ready_context(self) -> tuple[Path, IndexStore]:
        ...


class ServiceNavigationMixin:
    def overview(self, limit: int = 30) -> dict[str, Any]:
        service = cast(_NavigationService, self)
        service._store_context()
        files = service.view.all_files()
        return overview_result(files, limit)

    def tree_get(self, root_path: str = "", depth: int = 2, limit: int = 120) -> dict[str, Any]:
        service = cast(_NavigationService, self)
        service._store_context()
        files = service.view.all_files()
        return tree_result(files, root_path, depth, limit)

    def query(
        self,
        text: str = "",
        languages: list[str] | None = None,
        parent: str = "",
        limit: int = 80,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        service = cast(_NavigationService, self)
        service._store_context()
        page = PageRequest.from_cursor(cursor, limit)
        rows = service.view.query(text, languages or [], parent, page.fetch_limit, page.offset)
        next_cursor = page.next_cursor if len(rows) > page.limit else None
        return {
            "format": "auto_index_query_indexed",
            "items": [compact_file(row) for row in rows[:page.limit]],
            "cursor": next_cursor,
        }

    def file_summary(self, path: str) -> dict[str, Any]:
        service = cast(_NavigationService, self)
        service._store_context()
        lookup = service.view.get_file(path)
        if lookup.item is None:
            raise KeyError(f"indexed file not found: {path}")
        symbols = lookup.item["symbols"]
        return {
            "format": "auto_index_file_summary_full",
            "path": lookup.item["path"],
            "language": lookup.item["language"],
            "line_count": lookup.item["line_count"],
            "imports": lookup.item["imports"],
            "symbol_count": len(symbols),
            "symbols": symbols,
            "total_complexity": sum(symbol.get("complexity", 1) for symbol in symbols),
            "max_complexity": max((symbol.get("complexity", 1) for symbol in symbols), default=0),
        }

    def get(self, path: str) -> dict[str, Any]:
        service = cast(_NavigationService, self)
        service._store_context()
        lookup = service.view.get_file(path)
        if lookup.item is None:
            raise KeyError(f"indexed file not found: {path}")
        return {"format": "auto_index_get_full", "item": lookup.item}

    def file_content(self, path: str) -> str:
        service = cast(_NavigationService, self)
        root, _store = service._ready_context()
        return service.view.read_text(root, path)

    def resolve_path(self, path: str, limit: int = 20) -> dict[str, Any]:
        service = cast(_NavigationService, self)
        service._store_context()
        needle = path.lower().replace("\\", "/")
        matches = []
        for item in service.view.all_files():
            candidate = item["path"].lower()
            if is_glob_pattern(needle) and (Path(candidate).match(needle) or Path(candidate).name.lower() == needle):
                matches.append(compact_file(item))
                if len(matches) >= limit:
                    break
                continue
            if candidate == needle or item["name"].lower() == needle or needle in candidate:
                matches.append(compact_file(item))
                if len(matches) >= limit:
                    break
                continue
            symbols = item.get("symbols") or []
            symbol_names = [s["name"].lower() if isinstance(s, dict) else str(s).lower() for s in symbols]
            if needle in symbol_names:
                matches.append(compact_file(item))
                if len(matches) >= limit:
                    break
        return {"format": "auto_index_resolve_indexed", "items": matches}

    def diff_filesystem(self) -> dict[str, Any]:
        service = cast(_NavigationService, self)
        root, _store = service._ready_context()
        diff = service.view.diff_filesystem(root)
        added = diff["added"]
        deleted = diff["deleted"]
        changed = diff["changed"]
        return {
            "format": "auto_index_diff_indexed",
            "added": added[:100],
            "deleted": deleted[:100],
            "changed": changed[:100],
            "added_count": len(added),
            "deleted_count": len(deleted),
            "changed_count": len(changed),
        }

    def all_files(self) -> list[dict[str, Any]]:
        service = cast(_NavigationService, self)
        service._store_context()
        return service.view.all_files()
