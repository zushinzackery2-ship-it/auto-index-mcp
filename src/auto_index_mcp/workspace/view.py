from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..indexing.scanner import SourceScanner
from ..indexing.store import IndexStore
from ..core.text_decode import read_text_file
from .discovery import read_index_metadata
from .safety import ensure_relative_to

# Cache configuration - short TTL for incremental update responsiveness
_CACHE_TTL_SECONDS = 0.5


@dataclass(frozen=True)
class FileLookup:
    item: dict[str, Any] | None


class WorkspaceView:
    def __init__(
        self,
        store: IndexStore,
        visited_db_paths: set[Path] | None = None,
        ignore_patterns: list[str] | None = None,
        auto_ignore_patterns: list[str] | None = None,
        privileged_patterns: list[str] | None = None,
    ) -> None:
        self.store = store
        self.visited_db_paths = {path.resolve() for path in visited_db_paths or set()}
        self.visited_db_paths.add(store.db_path.resolve())
        self.ignore_patterns = ignore_patterns or []
        self.auto_ignore_patterns = auto_ignore_patterns or []
        self.privileged_patterns = privileged_patterns or []
        self._active_children: list[dict[str, Any]] | None = None
        self._child_stores: dict[str, IndexStore] = {}
        self._child_views: dict[str, WorkspaceView] = {}
        # Result caches with TTL
        self._cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
        self._cache_lock = threading.Lock()

    def all_files(self) -> list[dict[str, Any]]:
        return self._cached_files("all_files", lambda: self._load_all_files())

    def child_indexes(self) -> list[dict[str, Any]]:
        return self._active_child_indexes()

    def file_headers(self) -> list[dict[str, Any]]:
        return self._cached_files("file_headers", lambda: self._load_file_headers())

    def search_targets(self) -> list[dict[str, Any]]:
        return self._cached_files("search_targets", lambda: self._load_search_targets())

    def _cached_files(self, key: str, loader: Callable[[], list[dict[str, Any]]]) -> list[dict[str, Any]]:
        """Get cached result or load fresh data with TTL-based caching."""
        now = time.time()
        with self._cache_lock:
            cached = self._cache.get(key)
            if cached is not None and (now - cached[0]) < _CACHE_TTL_SECONDS:
                return cached[1]
        # Load fresh data outside lock
        result = loader()
        with self._cache_lock:
            self._cache[key] = (now, result)
        return result

    def _load_all_files(self) -> list[dict[str, Any]]:
        """Load all files from store and all child indexes."""
        files = self.store.all_files()
        for child in self._active_child_indexes():
            files.extend(self.prefixed_files(child))
        return sorted(files, key=lambda item: item["path"].lower())

    def _load_file_headers(self) -> list[dict[str, Any]]:
        """Load file headers from store and all child indexes."""
        files = self.store.file_headers()
        for child in self._active_child_indexes():
            files.extend(self.prefixed_file_headers(child))
        return sorted(files, key=lambda item: item["path"].lower())

    def _load_search_targets(self) -> list[dict[str, Any]]:
        """Load search targets from store and all child indexes."""
        targets = self.store.search_targets()
        for child in self._active_child_indexes():
            targets.extend(self.prefixed_search_targets(child))
        return sorted(targets, key=lambda item: item["path"].lower())

    def invalidate_cache(self) -> None:
        """Invalidate all caches (call after mutations)."""
        with self._cache_lock:
            self._cache.clear()
            self._active_children = None

    def query(self, text: str, languages: list[str], parent: str, limit: int, offset: int) -> list[dict[str, Any]]:
        rows = self.store.query(text, languages, parent, limit + offset, 0)
        for child in self._active_child_indexes():
            child_parent = self._child_parent_filter(child, parent)
            if child_parent is None:
                continue
            child_rows = self._child_view(child).query(text, languages, child_parent, limit + offset, 0)
            rows.extend(self.prefixed_files(child, child_rows))
        return sorted(rows, key=lambda item: item["path"].lower())[offset:offset + limit]

    def query_symbols(self, text: str, kind: str, limit: int, offset: int) -> list[dict[str, Any]]:
        rows = self.store.query_symbols(text, kind, limit + offset, 0)
        for child in self._active_child_indexes():
            child_rows = self._child_view(child).query_symbols(text, kind, limit + offset, 0)
            rows.extend(self._prefixed_symbols(child, child_rows))
        return sorted(rows, key=lambda item: (item["file_path"].lower(), item["line"]))[offset:offset + limit]

    def get_file(self, path: str) -> FileLookup:
        item = self.store.get_file(path)
        if item:
            return FileLookup(item)
        child, child_path = self.split_child_path(path)
        if not child:
            return FileLookup(None)
        lookup = self._child_view(child).get_file(child_path)
        if not lookup.item:
            return FileLookup(None)
        return FileLookup(self._prefix_file(child, lookup.item))

    def read_text(self, root: Path, path: str) -> str:
        lookup = self.get_file(path)
        if lookup.item:
            return self.read_indexed_text(root, lookup.item)
        target = ensure_relative_to(root / path, root, path)
        return read_text_file(target)

    def read_indexed_text(self, root: Path, item: dict[str, Any]) -> str:
        source_root = Path(item.get("source_root") or root).resolve()
        source_path = item.get("source_path", item["path"])
        target = ensure_relative_to(source_root / source_path, source_root, item["path"])
        return read_text_file(target)

    def context_for_match(self, root: Path, match: dict[str, Any], context_lines: int) -> list[dict[str, Any]]:
        lines = self.read_text(root, match["path"]).splitlines()
        line_index = match["line"] - 1
        start = max(0, line_index - context_lines)
        end = min(len(lines), line_index + context_lines + 1)
        return [{"line": index + 1, "text": lines[index]} for index in range(start, end)]

    def diff_filesystem(self, root: Path) -> dict[str, list[str]]:
        children = self._active_child_indexes()
        scan = SourceScanner(
            str(root),
            extra_excludes=self.ignore_patterns,
            auto_excludes=self.auto_ignore_patterns,
            privileged_patterns=self.privileged_patterns,
            boundary_roots=[Path(child["root"]) for child in children],
        ).scan()
        indexed = {item["path"]: item for item in self.store.all_files()}
        current = {item.path: item for item in scan.records}
        added = list(set(current) - set(indexed))
        deleted = list(set(indexed) - set(current))
        changed = [path for path in set(current) & set(indexed) if current[path].sha1 != indexed[path]["sha1"]]
        for child in children:
            child_diff = self._child_view(child).diff_filesystem(Path(child["root"]))
            added.extend(f"{child['path']}/{path}" for path in child_diff["added"])
            deleted.extend(f"{child['path']}/{path}" for path in child_diff["deleted"])
            changed.extend(f"{child['path']}/{path}" for path in child_diff["changed"])
        return {"added": sorted(added), "deleted": sorted(deleted), "changed": sorted(changed)}

    def split_child_path(self, path: str) -> tuple[dict[str, Any] | None, str]:
        normalized = path.replace("\\", "/").strip("/")
        for child in self._active_child_indexes():
            prefix = child["path"].rstrip("/")
            if normalized == prefix:
                return child, ""
            if normalized.startswith(prefix + "/"):
                return child, normalized[len(prefix) + 1:]
        return None, normalized

    def prefixed_files(self, child: dict[str, Any], files: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        rows = files if files is not None else self._child_view(child).all_files()
        return [self._prefix_file(child, item) for item in rows]

    def prefixed_file_headers(self, child: dict[str, Any], files: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        rows = files if files is not None else self._child_view(child).file_headers()
        return [self._prefix_file_header(child, item) for item in rows]

    def prefixed_search_targets(self, child: dict[str, Any], files: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        rows = files if files is not None else self._child_view(child).search_targets()
        return [self._prefix_search_target(child, item) for item in rows]

    def _prefix_file(self, child: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
        prefixed = dict(item)
        prefixed["source_root"] = item.get("source_root", child["root"])
        prefixed["source_path"] = item.get("source_path", item["path"])
        prefixed["path"] = f"{child['path']}/{item['path']}"
        prefixed["parent"] = str(Path(prefixed["path"]).parent).replace("\\", "/")
        if prefixed["parent"] == ".":
            prefixed["parent"] = ""
        prefixed["symbols"] = [self._prefix_symbol_refs(child, symbol) for symbol in prefixed["symbols"]]
        return prefixed

    def _prefix_file_header(self, child: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
        prefixed = dict(item)
        prefixed["source_root"] = item.get("source_root", child["root"])
        prefixed["source_path"] = item.get("source_path", item["path"])
        prefixed["path"] = f"{child['path']}/{item['path']}"
        prefixed["parent"] = str(Path(prefixed["path"]).parent).replace("\\", "/")
        if prefixed["parent"] == ".":
            prefixed["parent"] = ""
        return prefixed

    def _prefix_search_target(self, child: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
        return {
            "path": f"{child['path']}/{item['path']}",
            "language": item.get("language", ""),
            "active_source": item.get("active_source", True),
            "source_root": item.get("source_root", child["root"]),
            "source_path": item.get("source_path", item["path"]),
        }

    def _prefixed_symbols(self, child: dict[str, Any], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        prefixed = []
        for row in rows:
            item = self._prefix_symbol_refs(child, dict(row))
            item["file_path"] = f"{child['path']}/{item['file_path']}"
            prefixed.append(item)
        return prefixed

    def _prefix_symbol_refs(self, child: dict[str, Any], symbol: dict[str, Any]) -> dict[str, Any]:
        updated = dict(symbol)
        updated["called_by"] = [self._prefix_caller(child, value) for value in updated.get("called_by", [])]
        return updated

    def _prefix_caller(self, child: dict[str, Any], value: str) -> str:
        if "::" not in value:
            return value
        file_path, symbol = value.split("::", 1)
        if file_path.startswith(child["path"] + "/"):
            return value
        return f"{child['path']}/{file_path}::{symbol}"

    def _child_parent_filter(self, child: dict[str, Any], parent: str) -> str | None:
        parent = parent.replace("\\", "/").strip("/")
        prefix = child["path"].rstrip("/")
        if not parent:
            return ""
        if parent == prefix:
            return ""
        if parent.startswith(prefix + "/"):
            return parent[len(prefix) + 1:]
        if prefix.startswith(parent.rstrip("/") + "/"):
            return ""
        return None

    def _child_store(self, child: dict[str, Any]) -> IndexStore:
        db_path = Path(child["db_path"])
        if not db_path.exists() or not read_index_metadata(db_path):
            raise KeyError(f"child index is not available: {child['path']}")
        key = str(db_path.resolve())
        if key not in self._child_stores:
            self._child_stores[key] = IndexStore(db_path)
        return self._child_stores[key]

    def _child_view(self, child: dict[str, Any]) -> "WorkspaceView":
        key = str(Path(child["db_path"]).resolve())
        if key not in self._child_views:
            self._child_views[key] = WorkspaceView(
                self._child_store(child),
                self.visited_db_paths,
                self.ignore_patterns,
                self.auto_ignore_patterns,
                self.privileged_patterns,
            )
        return self._child_views[key]

    def _active_child_indexes(self) -> list[dict[str, Any]]:
        if self._active_children is not None:
            return self._active_children
        children = []
        for child in self.store.child_indexes():
            db_path = Path(child["db_path"]).resolve()
            if db_path.exists() and db_path not in self.visited_db_paths and read_index_metadata(db_path):
                children.append(child)
        self._active_children = children
        return children
