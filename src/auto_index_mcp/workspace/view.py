from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..storage.index import IndexStore
from ..languages.text_decode import read_text_file
from .discovery import read_index_metadata
from .safety import ensure_relative_to
from .filesystem import diff_local
from .ranking import ranked_symbol_key
from .projection import (
    prefix_file, prefix_file_header, prefix_search_target, prefixed_symbols,
)

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
        # Result caches keyed by published workspace generations
        self._cache: dict[str, tuple[tuple, list[dict[str, Any]]]] = {}
        self._cache_lock = threading.Lock()
        self._source_generation = None

    def all_files(self) -> list[dict[str, Any]]:
        return self._load_all_files()

    def child_indexes(self) -> list[dict[str, Any]]:
        return self._active_child_indexes()

    def iter_sources(self, root: Path):
        yield "", root, self.store
        for child in self._active_child_indexes():
            for prefix, source_root, store in self._child_view(child).iter_sources(Path(child["root"])):
                yield "/".join(part for part in (child["path"], prefix) if part), source_root, store

    def file_headers(self) -> list[dict[str, Any]]:
        return self._cached_files("file_headers", lambda: self._load_file_headers())

    def search_targets(self) -> list[dict[str, Any]]:
        return self._cached_files("search_targets", lambda: self._load_search_targets())

    def _cached_files(self, key: str, loader: Callable[[], list[dict[str, Any]]]) -> list[dict[str, Any]]:
        """Invalidate by published generations, including nested workspaces."""
        generation = self.generation_key()
        with self._cache_lock:
            cached = self._cache.get(key)
            if cached is not None and generation == cached[0]:
                return cached[1]
        # Load fresh data outside lock
        result = loader()
        with self._cache_lock:
            self._cache[key] = (generation, result)
        return result

    def generation_key(self) -> tuple:
        metadata = self.store.get_metadata_map()
        own = (metadata.get("index_epoch"), metadata.get("source_generation"), metadata.get("policy_generation"))
        if own != self._source_generation:
            self._source_generation = own
            self._active_children = None
        children = tuple((child["path"], self._child_view(child).generation_key()) for child in self._active_child_indexes())
        return own, children

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

    def query_symbols(self, text: str, kind: str, limit: int, offset: int) -> list[dict[str, Any]]:
        if not self._active_child_indexes():
            return self.store.query_symbols(text, kind, limit, offset)
        rows = self.store.query_symbols(text, kind, limit + offset, 0)
        for child in self._active_child_indexes():
            child_rows = self._child_view(child).query_symbols(text, kind, limit + offset, 0)
            rows.extend(prefixed_symbols(child, child_rows))
        # Text queries carry a match_rank tier from the store; merged
        # parent/child rows re-sort on it so relevance survives the merge.
        if text:
            rows.sort(key=ranked_symbol_key)
        else:
            rows.sort(key=lambda item: (item["file_path"].lower(), item["line"]))
        return rows[offset:offset + limit]

    def query_symbols_relaxed(self, subtokens: list[str], kind: str, limit: int, offset: int) -> list[dict[str, Any]]:
        if not self._active_child_indexes():
            return self.store.query_symbols_relaxed(subtokens, kind, limit, offset)
        rows = self.store.query_symbols_relaxed(subtokens, kind, limit + offset, 0)
        for child in self._active_child_indexes():
            child_rows = self._child_view(child).query_symbols_relaxed(subtokens, kind, limit + offset, 0)
            rows.extend(prefixed_symbols(child, child_rows))
        rows.sort(key=ranked_symbol_key)
        return rows[offset:offset + limit]

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
        return FileLookup(prefix_file(child, lookup.item))

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


    def diff_filesystem(self, root: Path) -> dict[str, list[str]]:
        children = self._active_child_indexes()
        added, deleted, changed = diff_local(self, root, children)
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
        return [prefix_file(child, item) for item in rows]

    def prefixed_file_headers(self, child: dict[str, Any], files: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        rows = files if files is not None else self._child_view(child).file_headers()
        return [prefix_file_header(child, item) for item in rows]

    def prefixed_search_targets(self, child: dict[str, Any], files: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        rows = files if files is not None else self._child_view(child).search_targets()
        return [prefix_search_target(child, item) for item in rows]


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
        metadata = self.store.get_metadata_map()
        generation = (metadata.get("index_epoch"), metadata.get("source_generation"), metadata.get("policy_generation"))
        if generation != self._source_generation:
            self._source_generation = generation
            self._active_children = None
        if self._active_children is not None:
            return self._active_children
        children = []
        for child in self.store.child_indexes():
            db_path = Path(child["db_path"]).resolve()
            if db_path.exists() and db_path not in self.visited_db_paths and read_index_metadata(db_path):
                children.append(child)
        self._active_children = children
        return children
