from __future__ import annotations

from pathlib import Path
import logging
from typing import Any

from ..domain.config import project_index_root
from ..indexing.tree_progress import TreeProgress
from ..storage.embeddings import EmbeddingStore
from ..storage.index import IndexStore
from ..registry import write_index_markers
from ..runtime.diagnostics import close_logging, configure_logging
from ..runtime.control.contracts import ActivationIntent
from ..runtime.locking.session import SessionLease
from ..domain.errors import ServiceError
from .maintenance import clear_index
from .attachment import attach_cache


class LifecycleCoordinator:
    def __init__(self, project, builds, ignore, embeddings, watch, state, stopping) -> None:
        self.project = project
        self.builds = builds
        self.ignore = ignore
        self.embeddings = embeddings
        self.watch = watch
        self.state = state
        self.stopping = stopping

    def enable(
        self,
        root_path: str,
        rebuild: bool = True,
        refresh_embedder: bool = True,
        source: str = "mcp-enable",
        activation=ActivationIntent.EXPLICIT,
    ) -> dict[str, Any]:
        root = Path(root_path).resolve()
        if self.project.fixed_root is not None and root != self.project.fixed_root:
            raise ServiceError("project-fixed", "this shared server is bound to a fixed project",
                               "connect to the server for the requested project",
                               root=str(self.project.fixed_root), requested_root=str(root))
        if not root.exists() or not root.is_dir():
            raise ValueError(f"root_path is not a directory: {root_path}")
        self.builds.cancel_auto_watch_after_build()
        if self.project.root_path and self.project.root_path != root:
            self.disable()
            self.project.embedding_indexer = None
            self.project.embedding_background = None
            self.project.background = None
            self.project._ignore_config_dirty = False
            from ..domain.ignore_config import IgnoreConfig
            self.project._ignore_config = IgnoreConfig()
        index_root = self.project.index_root_override or project_index_root(root)
        self.project.control.admission.check(root, activation)
        if self.project.quiescing and self.project.store is not None:
            self.stopping.stop(keep_control=True)
        self.project.quiescing = False
        self.project.lifecycle_cancelled.clear()
        self.project.binding_generation += 1
        self.project.control.start(root, index_root, self.project.registry.directory())
        if self._reusable_enable_context(root, index_root):
            if self.project.session_lease is None:
                self.project.session_lease = SessionLease(index_root, root=root, control=self.project.control)
            self.project.session_lease.acquire()
            if getattr(self.project, "log_path", None) is None:
                self.project.log_path = configure_logging(index_root)
            # Re-enable on the unchanged root: keep the live stores, watcher
            # and embedder. Recreating the stores here would issue a schema
            # write per call, which under multi-agent enable polling is pure
            # cross-process write-lock churn; only state another process may
            # have changed (persisted ignore config) is refreshed.
            self.project.enabled = True
            self.ignore._load_ignore_config_from_store()
            self.project._invalidate_view_cache()
            self.project.registry.touch(index_root)
            if refresh_embedder and self.project.embedding_indexer is None:
                self.embeddings._refresh_embedder()
            if rebuild:
                return self.builds.rebuild_sync()
            return self.state.status()
        session = attach_cache(self.project, root, index_root)
        try:
            self.project.log_path = configure_logging(index_root)
            store = IndexStore(self.project._db_path(root), session=session)
            store.initialize()
            embedding_store = EmbeddingStore(index_root / "embeddings.db", session=session)
            embedding_store.initialize()
        except BaseException:
            close_logging(getattr(self.project, "log_path", None))
            self.project.log_path = None
            session.release()
            raise
        self.project.session_lease = session
        self.project.root_path, self.project.index_root = root, index_root
        self.project.store, self.project.embedding_store = store, embedding_store
        self.project.enabled = True
        self._register_index(root, index_root, source)
        self.ignore._load_ignore_config_from_store()
        self.project.tree_progress = TreeProgress()
        if refresh_embedder:
            self.embeddings._refresh_embedder()
        else:
            self.project.embedding_indexer = None
        self.project._invalidate_view_cache()
        if rebuild:
            return self.builds.rebuild_sync()
        return self.state.status()

    def _reusable_enable_context(self, root: Path, index_root: Path) -> bool:
        return (
            self.project.root_path == root
            and self.project.index_root == index_root
            and self.project.store is not None
            and self.project.embedding_store is not None
            and self.project.store.db_path.exists()
        )

    def _register_index(self, root: Path, index_root: Path, source: str) -> None:
        """Best-effort registry upsert + marker drop; never fails enable."""
        try:
            self.project.registry.register(
                root,
                index_root,
                source=source,
                ephemeral=self.project.index_root_override is not None,
            )
            write_index_markers(index_root, root)
        except Exception:  # noqa: BLE001 - bookkeeping must not break enable
            logging.getLogger(__name__).exception("index registration failed root=%s index=%s", root, index_root)

    def enable_reusing_index(
        self,
        root_path: str,
        rebuild: bool = False,
        wait_seconds: float = 0.0,
        source: str = "mcp-enable",
        activation=ActivationIntent.EXPLICIT,
    ) -> dict[str, Any]:
        root = Path(root_path).resolve()
        self.project.control.admission.check(root, activation)
        if self._enable_already_running(root):
            return self._already_running_enable_status()
        if rebuild:
            # Explicit forced rebuild: dispatch to background thread and return immediately.
            self.enable(str(root), rebuild=False, refresh_embedder=False, source=source, activation=activation)
            return self.builds._start_background_rebuild(wait_seconds=wait_seconds)
        db_existed = self.project._db_path(root).exists()
        self.enable(str(root), rebuild=False, refresh_embedder=False, source=source, activation=activation)
        if db_existed and self.builds.can_reuse_index_for(root):
            # Reused a fresh index: build the vector store now (non-blocking) so
            # semantic search is ready without waiting for a first query to
            # trigger a lazy build.
            if self.project.semantic_auto_start or self.project.embedding_indexer is not None:
                self.embeddings.ensure_embedding_background()
            return self.state.status()
        return self.builds._start_background_rebuild(wait_seconds=wait_seconds, reuse_if_fresh=True)

    def _enable_already_running(self, root: Path) -> bool:
        background = self.project.background
        if background is None or not background.is_running():
            return False
        if not self.project.enabled or self.project.root_path is None:
            return False
        index_root = self.project.index_root_override or project_index_root(root)
        key = (root.resolve(), index_root.resolve())
        return (
            self.project.root_path.resolve() == root.resolve()
            and self.project.index_root is not None
            and self.project.index_root.resolve() == index_root.resolve()
            and self.project._background_context_key == key
        )

    def _already_running_enable_status(self) -> dict[str, Any]:
        result = self.state.status()
        result["status"] = "already-running"
        result["already_running"] = True
        return result

    def disable(self) -> dict[str, Any]:
        return self.stopping.stop()

    def clear(self, delete_file: bool = False) -> dict[str, Any]:
        return clear_index(self.project, self.watch, self.state, delete_file)
