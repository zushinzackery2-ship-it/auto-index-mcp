from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from ..runtime.background import (
    BackgroundIndexer,
)
from .index_policy import can_reuse_index, can_start_auto_watch_policy
from ..domain.ignore_config import IgnoreConfig
from .build_context import RebuildContext
from .ignore_metadata import service_ignore_fingerprint
from ..runtime.leases import BuildLock
from .build_pipeline import BuildPipeline


class RebuildCoordinator:
    def __init__(self, project, ignore, embeddings, state, start_watcher) -> None:
        self.project = project
        self.ignore = ignore
        self.embeddings = embeddings
        self.state = state
        self.start_watcher = start_watcher
        self.pipeline = BuildPipeline(project, ignore, embeddings)

    def rebuild(self, reuse_if_fresh: bool = False) -> dict[str, Any]:
        self.project._ready_context()
        assert self.project.index_root is not None
        if reuse_if_fresh and self._index_is_fresh():
            return self.state.status()
        return self._start_background_rebuild()

    def rebuild_sync(self, reuse_if_fresh: bool = False) -> dict[str, Any]:
        """Synchronous full rebuild for enable(rebuild=True) and lower-level callers.

        MCP tool entrypoints go through the background path so a large project
        never blocks the request thread past the host timeout; this variant keeps
        the original contract of an index that is fully built on return.
        """
        self.project._ready_context()
        assert self.project.index_root is not None
        if reuse_if_fresh and self._index_is_fresh():
            return self.state.status()
        context = self._rebuild_context()
        return self._rebuild_with_lock(context)

    def _rebuild_with_lock(
        self,
        context: RebuildContext,
        indexer: BackgroundIndexer | None = None,
    ) -> dict[str, Any]:
        """Acquire the writer lease before checking freshness and rebuilding."""
        lock = BuildLock(context.index_root / "index.build.lock")
        acquired = lock.try_acquire()
        try:
            if not acquired:
                if indexer is not None:
                    indexer.set_phase("waiting-for-writer")
                result = self.state.status()
                result["status"] = "indexing-in-other-process"
                result["rebuild"] = False
                result["message"] = "another auto-index process is still rebuilding this project"
                # Holder pid/liveness/heartbeat-age lets callers distinguish a
                # real concurrent build from a lock that is about to be
                # reclaimed, instead of trusting this message blindly.
                result["build_lock"] = lock.state_info()
                return result
            metadata = context.store.get_metadata_map()
            if int(metadata.get("policy_generation", 0)) != context.policy_generation:
                context = replace(context, ignore_config=IgnoreConfig.from_metadata(metadata),
                                  policy_generation=int(metadata.get("policy_generation", 0)))
            if context.reuse_if_fresh and can_reuse_index(context.store, context.root, service_ignore_fingerprint(self.project, context.root)):
                return dict(status="indexed", reused=True, file_count=context.store.get_metadata_map().get("file_count", 0))
            return self.pipeline.run(indexer, context)
        finally:
            lock.release()

    def _start_background_rebuild(self, wait_seconds: float = 0.0, reuse_if_fresh: bool = False) -> dict[str, Any]:
        context = replace(self._rebuild_context(), reuse_if_fresh=reuse_if_fresh)
        existing = self.project.background
        if existing is not None and existing.is_running() and self.project._background_context_key == context.key:
            completed = existing.wait(max(0.0, wait_seconds))
            if completed:
                return _completed_background_result(existing) or self.state._background_status()
            return self.state._background_status()
        indexer = BackgroundIndexer(
            lambda worker: self._run_rebuild_locked(worker, context),
            on_done=lambda result: self._on_background_done(result, context),
            root=context.root, index_root=context.index_root,
        )
        self.project.background = indexer
        self.project._background_context_key = context.key
        indexer.start()
        if indexer.wait(max(0.0, wait_seconds)):
            return _completed_background_result(indexer) or self.state._background_status()
        return self.state._background_status()

    def _run_rebuild_locked(self, indexer: BackgroundIndexer, context: RebuildContext) -> dict[str, Any]:
        revision = context.store.get_metadata_map().get("updated_at")
        result = self._rebuild_with_lock(context, indexer)
        while result.get("status") == "indexing-in-other-process" and self.project._context_is_current(context):
            updated = context.store.get_metadata_map().get("updated_at")
            if (context.reuse_if_fresh or updated != revision) and can_reuse_index(context.store, context.root, service_ignore_fingerprint(self.project, context.root)):
                return dict(status="indexed", reused=True, file_count=context.store.get_metadata_map().get("file_count", 0))
            if indexer.cancelled.wait(0.5):
                return dict(status="cancelled")
            result = self._rebuild_with_lock(context, indexer)
        return result

    def request_auto_watch_after_build(self) -> None:
        self.project._auto_watch_after_build = True
        self.project._auto_watch_context_key = self._rebuild_context().key

    def cancel_auto_watch_after_build(self) -> None:
        self.project._auto_watch_after_build = False
        self.project._auto_watch_context_key = None

    def _on_background_done(self, result: dict[str, Any], context: RebuildContext) -> None:
        if not self.project._auto_watch_after_build or self.project._auto_watch_context_key != context.key:
            return
        self.cancel_auto_watch_after_build()
        if not self.project._context_is_current(context):
            return
        if self.project.watcher is not None and self.project.watcher.is_running():
            return
        if not self.can_start_auto_watch(result):
            return
        try:
            self.start_watcher(wait_ready=False)
        except Exception as exc:  # noqa: BLE001 - watcher start is best-effort
            self.project.last_errors.append(f"auto-watch: {exc}")

    def _rebuild_context(self) -> RebuildContext:
        root, store = self.project._ready_context()
        assert self.project.index_root is not None
        metadata = store.prepare_build()
        config = IgnoreConfig.from_metadata(metadata) if IgnoreConfig.has_metadata(metadata) else self.ignore.ignore_config()
        return RebuildContext(root, self.project.index_root, store, self.project.embedding_indexer, config,
                              policy_generation=int(metadata.get("policy_generation", 0)),
                              source_generation=int(metadata.get("source_generation", 0)))

    def _index_is_fresh(self) -> bool:
        if self.project.root_path is None:
            return False
        return can_reuse_index(
            self.project.store,
            self.project.root_path,
            service_ignore_fingerprint(self.project, self.project.root_path),
        )

    def can_reuse_index_for(self, root: Path) -> bool:
        return can_reuse_index(
            self.project.store,
            root,
            service_ignore_fingerprint(self.project, root),
        )

    def can_start_auto_watch(self, result: dict[str, Any] | None) -> bool:
        if self.project.root_path is None:
            return False
        return can_start_auto_watch_policy(
            self.project.store,
            self.project.root_path,
            result,
            service_ignore_fingerprint(self.project, self.project.root_path),
        )


def _completed_background_result(indexer: BackgroundIndexer) -> dict[str, Any] | None:
    status = indexer.status()
    if status["state"] == "error":
        return dict(status="indexing-failed", error=status["error"])
    result = status.get("last_result")
    return result if isinstance(result, dict) else None
