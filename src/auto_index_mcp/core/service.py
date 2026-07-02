from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import project_index_root
from .service_ignore import ServiceIgnoreMixin
from .service_navigation import ServiceNavigationMixin
from .service_index_state import ServiceIndexStateMixin
from .service_quality import ServiceQualityMixin
from .service_rebuild import ServiceRebuildMixin
from .service_search import ServiceSearchMixin
from .service_semantic import ServiceSemanticMixin
from .service_state import ServiceBase
from .service_watcher import ServiceWatcherMixin
from .tree_progress import TreeProgress
from ..embedding.embedding_store import EmbeddingStore
from ..indexing.store import IndexStore


class AutoIndexService(
    ServiceNavigationMixin,
    ServiceIndexStateMixin,
    ServiceIgnoreMixin,
    ServiceSearchMixin,
    ServiceQualityMixin,
    ServiceSemanticMixin,
    ServiceRebuildMixin,
    ServiceWatcherMixin,
):
    """Single-root code index service.

    Owns lifecycle (enable/disable/clear/status); shared state and the
    smallest helpers live in ServiceBase (which every mixin extends), and
    rebuild orchestration, the filesystem watcher, and the search/navigation
    tool surfaces live in the composed mixins.
    """

    def enable(
        self,
        root_path: str,
        rebuild: bool = True,
        refresh_embedder: bool = True,
    ) -> dict[str, Any]:
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
        self.embedding_store = EmbeddingStore(self.index_root / "embeddings.db")
        self.embedding_store.initialize()
        self._load_ignore_config_from_store()
        self.tree_progress = TreeProgress()
        if refresh_embedder:
            self._refresh_embedder()
        else:
            self.embedding_indexer = None
        self._invalidate_view_cache()
        if rebuild:
            return self.rebuild_sync()
        return self.status()

    def enable_reusing_index(
        self,
        root_path: str,
        rebuild: bool = False,
        wait_seconds: float = 0.0,
    ) -> dict[str, Any]:
        root = Path(root_path).resolve()
        if self._enable_already_running(root):
            return self._already_running_enable_status()
        if rebuild:
            # Explicit forced rebuild: dispatch to background thread and return immediately.
            self.enable(str(root), rebuild=False, refresh_embedder=False)
            return self._start_background_rebuild(wait_seconds=wait_seconds)
        db_existed = self._db_path(root).exists()
        result = self.enable(str(root), rebuild=False, refresh_embedder=False)
        if db_existed and self.can_reuse_index_for(root):
            # Reused a fresh index: build the vector store now (non-blocking) so
            # semantic search is ready without waiting for a first query to
            # trigger a lazy build.
            self.ensure_embedding_background()
            return self.status()
        return self._start_background_rebuild(wait_seconds=wait_seconds)

    def _enable_already_running(self, root: Path) -> bool:
        background = self.background
        if background is None or not background.is_running():
            return False
        if not self.enabled or self.root_path is None:
            return False
        index_root = self.index_root_override or project_index_root(root)
        key = (root.resolve(), index_root.resolve())
        return (
            self.root_path.resolve() == root.resolve()
            and self.index_root is not None
            and self.index_root.resolve() == index_root.resolve()
            and self._background_context_key == key
        )

    def _already_running_enable_status(self) -> dict[str, Any]:
        result = self.status()
        result["status"] = "already-running"
        result["already_running"] = True
        return result

    def disable(self) -> dict[str, Any]:
        self.cancel_auto_watch_after_build()
        self.stop_watcher()
        self.tree_progress.clear()
        self.enabled = False
        result = self.status()
        if self.background is not None and self.background.is_running():
            result["warning"] = "background index build still running on its daemon thread"
        return result

    def status(self) -> dict[str, Any]:
        store = self.store
        meta = store.get_metadata_map() if store else {}
        file_count = int(meta.get("file_count") or 0)
        total_file_count = int(meta.get("total_file_count") or file_count)
        result: dict[str, Any] = {
            "enabled": self.enabled,
            "root": str(self.root_path) if self.root_path else None,
            "index_path": str(store.db_path) if store else None,
            "file_count": file_count,
            "total_file_count": total_file_count,
            "child_index_count": int(meta.get("child_index_count") or 0),
            "updated_at": meta.get("updated_at"),
            "last_error_count": len(self.last_errors),
            "last_errors": self.last_errors[:10],
            "watcher": self.watcher_status(),
            "embedding": {
                "enabled": self.embedding_indexer is not None,
                "model": self.embedding_indexer.backend.name if self.embedding_indexer is not None else None,
            },
            "build_timers": self.build_timers(),
        }
        if self.background is not None:
            result["background_index"] = self.background.status()
        if self.embedding_background is not None:
            result["embedding_background"] = self.embedding_background.status()
        return result

    def clear(self, delete_file: bool = False) -> dict[str, Any]:
        if self.background is not None and self.background.is_running():
            result = self.status()
            result["status"] = "clear-skipped-background-running"
            result["message"] = "background index build is still running; clear was not started"
            return result
        if self.embedding_background is not None and self.embedding_background.is_running():
            result = self.status()
            result["status"] = "clear-skipped-embedding-running"
            result["message"] = "embedding build is still running; clear was not started"
            return result
        store = self._store_context()
        if delete_file:
            self.stop_watcher()
            store.delete_file()
            self.store = None
            self.enabled = False
            if self.embedding_store is not None:
                self.embedding_store.delete_file()
                self.embedding_store = None
            self.embedding_indexer = None
        else:
            store.clear()
            if self.embedding_store is not None:
                self.embedding_store.clear()
        self.tree_progress.clear()
        self._invalidate_view_cache()
        return self.status()
