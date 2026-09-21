from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import project_index_root
from .timefmt import iso_time
from .tool_errors import ENABLE_HINT
from .service_embedding import ServiceEmbeddingMixin
from .service_ignore import ServiceIgnoreMixin
from .service_navigation import ServiceNavigationMixin
from .service_index_state import ServiceIndexStateMixin
from .service_quality import ServiceQualityMixin
from .service_rebuild import ServiceRebuildMixin
from .service_search import ServiceSearchMixin
from .service_semantic import ServiceSemanticMixin
from .service_watcher import ServiceWatcherMixin
from .tree_progress import TreeProgress
from .maintenance import clear_index
from ..embedding.embedding_store import EmbeddingStore
from ..indexing.store import IndexStore
from ..registry import write_index_markers
from ..runtime.diagnostics import close_logging, configure_logging


class AutoIndexService(
    ServiceNavigationMixin,
    ServiceIndexStateMixin,
    ServiceIgnoreMixin,
    ServiceSearchMixin,
    ServiceQualityMixin,
    ServiceSemanticMixin,
    ServiceRebuildMixin,
    ServiceEmbeddingMixin,
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
        source: str = "mcp-enable",
    ) -> dict[str, Any]:
        root = Path(root_path).resolve()
        if not root.exists() or not root.is_dir():
            raise ValueError(f"root_path is not a directory: {root_path}")
        self.cancel_auto_watch_after_build()
        if self.root_path and self.root_path != root:
            self.enabled = False
            self._cancel_background_work()
            self.stop_watcher()
            self.embedding_indexer = None
            self.embedding_background = None
            self.background = None
            self._ignore_config_dirty = False
            from .ignore_config import IgnoreConfig
            self._ignore_config = IgnoreConfig()
        index_root = self.index_root_override or project_index_root(root)
        if self._reusable_enable_context(root, index_root):
            # Re-enable on the unchanged root: keep the live stores, watcher
            # and embedder. Recreating the stores here would issue a schema
            # write per call, which under multi-agent enable polling is pure
            # cross-process write-lock churn; only state another process may
            # have changed (persisted ignore config) is refreshed.
            self.enabled = True
            self._load_ignore_config_from_store()
            self._invalidate_view_cache()
            self.registry.touch(root)
            if refresh_embedder and self.embedding_indexer is None:
                self._refresh_embedder()
            if rebuild:
                return self.rebuild_sync()
            return self.status()
        self.log_path = configure_logging(index_root)
        store = IndexStore(self._db_path(root))
        store.initialize()
        embedding_store = EmbeddingStore(index_root / "embeddings.db")
        embedding_store.initialize()
        self.root_path, self.index_root = root, index_root
        self.store, self.embedding_store = store, embedding_store
        self.enabled = True
        self._register_index(root, index_root, source)
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

    def _reusable_enable_context(self, root: Path, index_root: Path) -> bool:
        return (
            self.root_path == root
            and self.index_root == index_root
            and self.store is not None
            and self.embedding_store is not None
            and self.store.db_path.exists()
        )

    def _register_index(self, root: Path, index_root: Path, source: str) -> None:
        """Best-effort registry upsert + marker drop; never fails enable."""
        try:
            self.registry.register(
                root,
                index_root,
                source=source,
                ephemeral=self.index_root_override is not None,
            )
            write_index_markers(index_root, root)
        except Exception:  # noqa: BLE001 - bookkeeping must not break enable
            pass

    def enable_reusing_index(
        self,
        root_path: str,
        rebuild: bool = False,
        wait_seconds: float = 0.0,
        source: str = "mcp-enable",
    ) -> dict[str, Any]:
        root = Path(root_path).resolve()
        if self._enable_already_running(root):
            return self._already_running_enable_status()
        if rebuild:
            # Explicit forced rebuild: dispatch to background thread and return immediately.
            self.enable(str(root), rebuild=False, refresh_embedder=False, source=source)
            return self._start_background_rebuild(wait_seconds=wait_seconds)
        db_existed = self._db_path(root).exists()
        self.enable(str(root), rebuild=False, refresh_embedder=False, source=source)
        if db_existed and self.can_reuse_index_for(root):
            # Reused a fresh index: build the vector store now (non-blocking) so
            # semantic search is ready without waiting for a first query to
            # trigger a lazy build.
            if self.semantic_auto_start or self.embedding_indexer is not None:
                self.ensure_embedding_background()
            return self.status()
        return self._start_background_rebuild(wait_seconds=wait_seconds, reuse_if_fresh=True)

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
        self.enabled = False
        self._cancel_background_work()
        self.cancel_auto_watch_after_build()
        self.stop_watcher()
        self.tree_progress.clear()
        result = self.status()
        self.embedding_indexer = None
        close_logging(getattr(self, "log_path", None))
        if self.background is not None and self.background.is_running():
            result["warning"] = "background index build still running on its daemon thread"
        return result

    def _cancel_background_work(self) -> None:
        for worker in (self.background, self.embedding_background):
            if worker is not None:
                worker.cancelled.set()

    def status(self) -> dict[str, Any]:
        """Compact index health: one level of nesting, ISO timestamps, no
        duplicated background result blobs (LLM callers read this a lot)."""
        store = self.store
        meta = store.get_metadata_map() if store else {}
        file_count = int(meta.get("file_count") or 0)
        total_file_count = int(meta.get("total_file_count") or file_count)
        timers = self.build_timers()
        result: dict[str, Any] = {
            "enabled": self.enabled,
            "root": str(self.root_path) if self.root_path else None,
            "index_path": str(store.db_path) if store else None,
            "log_path": getattr(self, "log_path", None),
            "file_count": file_count,
            "total_file_count": total_file_count,
            "child_index_count": int(meta.get("child_index_count") or 0),
            "updated_at": iso_time(meta.get("updated_at")),
            "watcher": self.watcher_status(),
            "embedding": self._compact_embedding_status(timers["embedding"]),
            "index_build": _compact_timer(timers["index"]),
        }
        if self.root_path is not None:
            result["registered"] = self.registry.is_registered(self.root_path)
        if self.last_errors:
            result["error_count"] = len(self.last_errors)
            result["errors"] = self.last_errors[:5]
        if store is None or self.root_path is None:
            result["hint"] = ENABLE_HINT
        return result

    def _compact_embedding_status(self, timer: dict[str, Any]) -> dict[str, Any]:
        """Semantic-vector state merged into status (was a separate tool)."""
        indexer = self.embedding_indexer
        result: dict[str, Any] = {
            "enabled": indexer is not None,
            "model": indexer.backend.name if indexer is not None else None,
        }
        if not self.semantic_enabled:
            result["state"] = "disabled"
            return result
        if timer.get("running"):
            result["state"] = "building"
            result["elapsed_seconds"] = timer.get("elapsed_seconds")
            return result
        if indexer is None:
            result["state"] = "on-demand"
            return result
        try:
            count = indexer.count()
        except Exception as exc:
            result["state"] = "error"
            result["error"] = str(exc)
            return result
        result["vector_count"] = count
        result["state"] = "ready" if count > 0 else "empty"
        return result

    def clear(self, delete_file: bool = False) -> dict[str, Any]:
        return clear_index(self, delete_file)


def _compact_timer(timer: dict[str, Any]) -> dict[str, Any]:
    return {
        "state": timer.get("state"),
        "phase": timer.get("phase"),
        "running": bool(timer.get("running")),
        "elapsed_seconds": timer.get("elapsed_seconds"),
    }
