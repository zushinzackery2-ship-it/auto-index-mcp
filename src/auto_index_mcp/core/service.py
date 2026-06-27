from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .config import DEFAULT_BUILD_LOCK_WAIT_SECONDS, project_index_root
from .background_indexer import BackgroundIndexer
from .service_navigation import ServiceNavigationMixin
from .service_index_state import ServiceIndexStateMixin
from .service_quality import ServiceQualityMixin
from .service_rebuild import ServiceRebuildMixin
from .service_search import ServiceSearchMixin
from .service_semantic import ServiceSemanticMixin
from .service_watcher import ServiceWatcherMixin
from ..embedding.indexer import SymbolEmbedder
from ..workspace.view import WorkspaceView
from ..indexing.store import IndexStore
from ..indexing.watcher import FileEventWatcher


# View cache TTL - must be <= WorkspaceView cache TTL for consistency
_VIEW_CACHE_TTL_SECONDS = 0.5


class AutoIndexService(
    ServiceNavigationMixin,
    ServiceIndexStateMixin,
    ServiceSearchMixin,
    ServiceQualityMixin,
    ServiceSemanticMixin,
    ServiceRebuildMixin,
    ServiceWatcherMixin,
):
    """Single-root code index service.

    Owns lifecycle (enable/disable/clear/status) and shared infrastructure
    (store, cached WorkspaceView, readiness guards); rebuild orchestration,
    the filesystem watcher, and the search/navigation tool surfaces live in
    the composed mixins.
    """

    def __init__(self, index_root: Path | None = None) -> None:
        self.index_root_override = index_root
        self.index_root: Path | None = index_root
        self.root_path: Path | None = None
        self.enabled = False
        self.last_errors: list[str] = []
        self.store: IndexStore | None = None
        self.watcher: FileEventWatcher | None = None
        self.embedding_indexer: SymbolEmbedder | None = None
        # Background full-tree rebuild runner. Stays None when the project is
        # enabled against a reusable existing index (fast path) or while idle.
        self.background: BackgroundIndexer | None = None
        # Set when a watcher should auto-start as soon as a background build ends.
        self._auto_watch_after_build = False
        self._auto_watch_context_key: tuple[Path, Path] | None = None
        self._background_context_key: tuple[Path, Path] | None = None
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
        self.cancel_auto_watch_after_build()
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
            return self.rebuild_sync()
        return self.status()

    def enable_reusing_index(self, root_path: str, rebuild: bool = False) -> dict[str, Any]:
        root = Path(root_path).resolve()
        if rebuild:
            # Explicit forced rebuild: dispatch to background thread and return immediately.
            self.enable(str(root), rebuild=False)
            return self._start_background_rebuild()
        db_existed = self._db_path(root).exists()
        result = self.enable(str(root), rebuild=False)
        if db_existed and self.can_reuse_index_for(root):
            return self.status()
        return self._start_background_rebuild()

    def disable(self) -> dict[str, Any]:
        self.cancel_auto_watch_after_build()
        self.stop_watcher()
        self.enabled = False
        result = self.status()
        if self.background is not None and self.background.is_running():
            result["warning"] = "background index build still running on its daemon thread"
        return result

    def status(self) -> dict[str, Any]:
        store = self.store
        meta = store.get_metadata_map() if store else {}
        child_indexes = store.child_indexes() if store else []
        result: dict[str, Any] = {
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
        if self.background is not None:
            result["background_index"] = self.background.status()
        return result

    def clear(self, delete_file: bool = False) -> dict[str, Any]:
        if self.background is not None and self.background.is_running():
            # The daemon worker cannot be interrupted mid-write; let its
            # replace_all settle so it does not race the clear below.
            self.background.wait(DEFAULT_BUILD_LOCK_WAIT_SECONDS)
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
