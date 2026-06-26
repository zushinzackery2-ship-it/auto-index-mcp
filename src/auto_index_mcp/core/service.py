from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .config import DEFAULT_BUILD_LOCK_WAIT_SECONDS, DEFAULT_WATCH_DEBOUNCE_SECONDS, INDEX_VERSION, project_index_root
from .index_policy import can_reuse_index, can_start_auto_watch
from .quality_dangling import with_project_quality_findings
from .service_navigation import ServiceNavigationMixin
from .service_quality import ServiceQualityMixin
from .service_search import ServiceSearchMixin
from .service_semantic import ServiceSemanticMixin
from ..embedding.backend import create_embedder
from ..embedding.indexer import SymbolEmbedder
from ..workspace.view import WorkspaceView
from ..indexing.analysis import resolve_project_callers
from ..indexing.active_sources import annotate_active_sources
from ..indexing.scanner import SourceScanner
from ..indexing.snapshot import snapshot_from_index, take_watch_snapshot, update_watch_snapshot
from ..indexing.build_lock import BuildLock
from ..indexing.store import IndexStore
from ..indexing.updater import IndexUpdater
from ..indexing.watcher import FileEventWatcher
from ..workspace.discovery import child_indexes_to_dicts, discover_child_indexes


# View cache TTL - must be <= WorkspaceView cache TTL for consistency
_VIEW_CACHE_TTL_SECONDS = 0.5


class AutoIndexService(ServiceNavigationMixin, ServiceSearchMixin, ServiceQualityMixin, ServiceSemanticMixin):
    def __init__(self, index_root: Path | None = None) -> None:
        self.index_root_override = index_root
        self.index_root: Path | None = index_root
        self.root_path: Path | None = None
        self.enabled = False
        self.last_errors: list[str] = []
        self.store: IndexStore | None = None
        self.watcher: FileEventWatcher | None = None
        self.embedding_indexer: SymbolEmbedder | None = None
        # Use a shared view with TTL-based caching for better incremental update responsiveness
        self._view: WorkspaceView | None = None
        self._view_created_at: float = 0.0

    @property
    def file_count(self) -> int:
        """Cached file count from metadata, avoid full all_files() call."""
        self._store_context()
        assert self.store is not None
        return len(self.store.search_targets())

    @property
    def view(self) -> WorkspaceView:
        """Get WorkspaceView with TTL-based caching."""
        self._store_context()
        now = time.time()
        if self._view is None or (now - self._view_created_at) > _VIEW_CACHE_TTL_SECONDS:
            self._view = WorkspaceView(self._store_context())
            self._view_created_at = now
        return self._view

    def _invalidate_view_cache(self) -> None:
        """Invalidate the cached view after mutations."""
        self._view = None

    def enable(self, root_path: str, rebuild: bool = True) -> dict[str, Any]:
        root = Path(root_path).resolve()
        if not root.exists() or not root.is_dir():
            raise ValueError(f"root_path is not a directory: {root_path}")
        if self.root_path and self.root_path != root:
            self.stop_watcher()
        self.root_path = root
        self.enabled = True
        self.index_root = self.index_root_override or project_index_root(root)
        self.store = IndexStore(self._db_path(root))
        self.store.initialize()
        self._refresh_embedder()
        self._invalidate_view_cache()
        if rebuild:
            return self.rebuild()
        return self.status()

    def enable_reusing_index(self, root_path: str, rebuild: bool = False) -> dict[str, Any]:
        root = Path(root_path).resolve()
        if rebuild:
            return self.enable(str(root), rebuild=True)
        db_existed = self._db_path(root).exists()
        result = self.enable(str(root), rebuild=False)
        if db_existed and self.can_reuse_index_for(root):
            return self.status()
        return self.rebuild(reuse_if_fresh=True)

    def disable(self) -> dict[str, Any]:
        self.stop_watcher()
        self.enabled = False
        return self.status()

    def rebuild(self, reuse_if_fresh: bool = False) -> dict[str, Any]:
        self._ready_context()
        assert self.index_root is not None
        lock = BuildLock(self.index_root / "index.build.lock")
        acquired = lock.acquire(DEFAULT_BUILD_LOCK_WAIT_SECONDS)
        try:
            if not acquired:
                result = self.status()
                result["status"] = "build-lock-timeout"
                result["rebuild"] = False
                result["message"] = "another auto-index process is still rebuilding this project"
                return result
            if reuse_if_fresh and self._index_is_fresh():
                return self.status()
            return self._rebuild_now()
        finally:
            lock.release()

    def _rebuild_now(self) -> dict[str, Any]:
        root, store = self._ready_context()
        start = time.time()
        try:
            metadata = store.get_metadata_map()
            existing = {item["path"]: item for item in store.all_files()} if metadata.get("version") == INDEX_VERSION else {}
        except Exception:
            existing = {}
        children = discover_child_indexes(root, store.db_path)
        boundary_roots = [Path(child.root) for child in children]
        scan = SourceScanner(str(root), existing_records=existing, boundary_roots=boundary_roots).scan()
        active_records = annotate_active_sources(root, scan.records)
        records = with_project_quality_findings(resolve_project_callers(active_records))
        store.replace_all(scan.root, records, child_indexes_to_dicts(children))
        self.last_errors = scan.errors[:50]
        embedding_meta = self._embed_after_full_rebuild(root)
        return {
            "status": "indexed",
            "root": scan.root,
            "file_count": len(records),
            "total_file_count": len(records) + sum(child.file_count for child in children),
            "child_index_count": len(children),
            "skipped": scan.skipped,
            "reused": scan.reused,
            "error_count": len(scan.errors),
            "elapsed_seconds": round(time.time() - start, 3),
            "index_path": str(store.db_path),
            "updated_at": store.get_metadata_map().get("updated_at"),
            "embedding": embedding_meta,
        }

    def _index_is_fresh(self) -> bool:
        return can_reuse_index(self.store, self.root_path)

    def can_reuse_index_for(self, root: Path) -> bool:
        return can_reuse_index(self.store, root)

    def can_start_auto_watch(self, result: dict[str, Any] | None) -> bool:
        return can_start_auto_watch(self.store, self.root_path, result)

    def status(self) -> dict[str, Any]:
        store = self.store
        meta = store.get_metadata_map() if store else {}
        child_indexes = store.child_indexes() if store else []
        return {
            "enabled": self.enabled,
            "root": str(self.root_path) if self.root_path else None,
            "index_path": str(store.db_path) if store else None,
            "file_count": meta.get("file_count", 0),
            "total_file_count": meta.get("file_count", 0) + sum(child["file_count"] for child in child_indexes),
            "child_index_count": meta.get("child_index_count", 0),
            "updated_at": meta.get("updated_at"),
            "last_error_count": len(self.last_errors),
            "last_errors": self.last_errors[:10],
        }

    def clear(self, delete_file: bool = False) -> dict[str, Any]:
        store = self._store_context()
        if delete_file:
            self.stop_watcher()
            store.delete_file()
            self.store = None
            self.enabled = False
        else:
            store.clear()
        self._invalidate_view_cache()
        return self.status()

    def start_watcher(self, debounce_seconds: float = DEFAULT_WATCH_DEBOUNCE_SECONDS, wait_ready: bool = True) -> dict[str, Any]:
        root, store = self._ready_context()
        if debounce_seconds < 0.05:
            raise ValueError("debounce_seconds must be >= 0.05")
        if (
            self.watcher
            and self.watcher.is_running()
            and self.watcher.root.resolve() == root.resolve()
            and self.watcher.debounce_seconds == debounce_seconds
        ):
            return self.watcher_status()
        if self.watcher:
            self.watcher.stop()
        children = lambda: [Path(child["root"]) for child in store.child_indexes()]
        snapshot = lambda: take_watch_snapshot(root, children(), store.db_path)
        update_snapshot = lambda previous, paths: update_watch_snapshot(root, previous, paths, children(), store.db_path)
        previous = snapshot_from_index(root, store.file_headers(), store.child_indexes())
        self.watcher = FileEventWatcher(
            root,
            snapshot,
            update_snapshot,
            self._make_watch_updater(root, store),
            debounce_seconds,
            previous,
        )
        self.watcher.start(wait_ready=wait_ready)
        return self.watcher_status()

    def sync_index_to_filesystem(self) -> dict[str, Any]:
        root, store = self._ready_context()
        child_indexes = store.child_indexes()
        child_roots = [Path(child["root"]) for child in child_indexes]
        previous = snapshot_from_index(root, store.file_headers(), child_indexes)
        current = take_watch_snapshot(root, child_roots, store.db_path)
        return IndexUpdater(root, store, self.rebuild).apply(previous, current)

    def stop_watcher(self) -> dict[str, Any]:
        if self.watcher:
            self.watcher.stop()
            self.watcher = None
        return self.watcher_status()

    def watcher_status(self) -> dict[str, Any]:
        if not self.watcher:
            return {"running": False}
        return self.watcher.status()

    def _make_watch_updater(self, root: Path, store: IndexStore):
        updater = IndexUpdater(root, store, self.rebuild)

        def apply(previous, current):
            result = updater.apply(previous, current)
            self._embed_after_incremental(root, store, previous, current, result)
            return result

        return apply

    def _refresh_embedder(self) -> None:
        store = self.store
        if store is None:
            self.embedding_indexer = None
            return
        backend = create_embedder()
        self.embedding_indexer = SymbolEmbedder(backend, store) if backend is not None else None

    def _embed_after_full_rebuild(self, root: Path) -> dict[str, Any] | None:
        indexer = self.embedding_indexer
        store = self.store
        if indexer is None or store is None:
            return None
        try:
            return indexer.embed_project(root, store.all_symbols())
        except Exception as exc:
            self.last_errors.append(f"embedding-rebuild: {exc}")
            return {"error": str(exc)}

    def _embed_after_incremental(self, root: Path, store: IndexStore, previous, current, result: dict[str, Any]) -> None:
        indexer = self.embedding_indexer
        if indexer is None:
            return
        status = result.get("status")
        if status in ("structural-rebuild", "indexed", "build-lock-timeout", "shared-index-current"):
            return
        if status != "incremental":
            return
        added, deleted, modified = current.changed_files(previous)
        changed = set(added) | set(modified)
        if changed:
            symbols = [s for s in store.all_symbols() if s["file_path"] in changed]
            grouped: dict[str, list[dict[str, Any]]] = {}
            for symbol in symbols:
                grouped.setdefault(symbol["file_path"], []).append(symbol)
            if grouped:
                try:
                    indexer.embed_files(root, grouped)
                except Exception as exc:
                    self.last_errors.append(f"embedding-incremental: {exc}")
        if deleted:
            try:
                with store.connect() as conn:
                    for path in deleted:
                        indexer.store.delete_file(conn, path)
            except Exception as exc:
                self.last_errors.append(f"embedding-delete: {exc}")

    def _db_path(self, root: Path) -> Path:
        index_root = self.index_root_override or project_index_root(root)
        return index_root / "index.db"

    def _require_ready(self) -> None:
        self._store_context()
        if self.root_path is None:
            raise RuntimeError("auto-index root is not configured")

    def _require_store(self) -> None:
        if self.store is None:
            raise RuntimeError("auto-index is not enabled")

    def _store_context(self) -> IndexStore:
        self._require_store()
        assert self.store is not None
        return self.store

    def _ready_context(self) -> tuple[Path, IndexStore]:
        self._require_ready()
        assert self.root_path is not None
        assert self.store is not None
        return self.root_path, self.store
