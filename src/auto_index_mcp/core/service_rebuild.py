from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from .config import INDEX_VERSION
from .background_indexer import (
    BackgroundIndexer,
    PHASE_ANALYZING,
    PHASE_DONE,
    PHASE_EMBEDDING,
    PHASE_SCANNING,
    PHASE_WRITING,
    STATE_DONE,
)
from .index_policy import can_reuse_index, can_start_auto_watch_policy
from .ignore_config import exact_path_pattern
from .quality_dangling import with_project_quality_findings
from .rebuild_context import RebuildContext
from .service_rebuild_ignore import config_ignore_metadata, service_ignore_fingerprint
from .service_state import ServiceBase
from .timefmt import iso_time
from .tree_progress import TreeProgress
from ..indexing.analysis import resolve_project_callers
from ..indexing.active_sources import annotate_active_sources
from ..indexing.scanner import SourceScanner
from ..indexing.build_lock import BuildLock
from ..workspace.discovery import child_indexes_to_dicts, discover_child_indexes


class ServiceRebuildMixin(ServiceBase):
    """Full-tree rebuild orchestration.

    Owns background dispatch, cross-process build-lock handling, per-phase
    progress reporting, and the index-state envelopes that keep search
    responses honest while a build is in flight. Concrete state
    (store/root_path/background/...) lives in ServiceBase; the watcher and
    embedding hooks come from their mixins through the shared MRO.
    """

    def rebuild(self, reuse_if_fresh: bool = False) -> dict[str, Any]:
        self._ready_context()
        assert self.index_root is not None
        if reuse_if_fresh and self._index_is_fresh():
            return self.status()
        return self._start_background_rebuild()

    def rebuild_sync(self, reuse_if_fresh: bool = False) -> dict[str, Any]:
        """Synchronous full rebuild for enable(rebuild=True) and lower-level callers.

        MCP tool entrypoints go through the background path so a large project
        never blocks the request thread past the host timeout; this variant keeps
        the original contract of an index that is fully built on return.
        """
        self._ready_context()
        assert self.index_root is not None
        if reuse_if_fresh and self._index_is_fresh():
            return self.status()
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
                result = self.status()
                result["status"] = "indexing-in-other-process"
                result["rebuild"] = False
                result["message"] = "another auto-index process is still rebuilding this project"
                # Holder pid/liveness/heartbeat-age lets callers distinguish a
                # real concurrent build from a lock that is about to be
                # reclaimed, instead of trusting this message blindly.
                result["build_lock"] = lock.state_info()
                return result
            if context.reuse_if_fresh and can_reuse_index(context.store, context.root, service_ignore_fingerprint(self, context.root)):
                return dict(status="indexed", reused=True, file_count=context.store.get_metadata_map().get("file_count", 0))
            return self._rebuild_now(indexer, context)
        finally:
            lock.release()

    def _start_background_rebuild(self, wait_seconds: float = 0.0, reuse_if_fresh: bool = False) -> dict[str, Any]:
        context = replace(self._rebuild_context(), reuse_if_fresh=reuse_if_fresh)
        existing = self.background
        if existing is not None and existing.is_running() and self._background_context_key == context.key:
            completed = existing.wait(max(0.0, wait_seconds))
            if completed:
                return _completed_background_result(existing) or self._background_status()
            return self._background_status()
        indexer = BackgroundIndexer(
            lambda worker: self._run_rebuild_locked(worker, context),
            on_done=lambda result: self._on_background_done(result, context),
        )
        self.background = indexer
        self._background_context_key = context.key
        indexer.start()
        if indexer.wait(max(0.0, wait_seconds)):
            return _completed_background_result(indexer) or self._background_status()
        return self._background_status()

    def _run_rebuild_locked(self, indexer: BackgroundIndexer, context: RebuildContext) -> dict[str, Any]:
        revision = context.store.get_metadata_map().get("updated_at")
        result = self._rebuild_with_lock(context, indexer)
        while result.get("status") == "indexing-in-other-process" and self._context_is_current(context):
            updated = context.store.get_metadata_map().get("updated_at")
            if (context.reuse_if_fresh or updated != revision) and can_reuse_index(context.store, context.root, service_ignore_fingerprint(self, context.root)):
                return dict(status="indexed", reused=True, file_count=context.store.get_metadata_map().get("file_count", 0))
            if indexer.cancelled.wait(0.5):
                return dict(status="cancelled")
            result = self._rebuild_with_lock(context, indexer)
        return result

    def request_auto_watch_after_build(self) -> None:
        self._auto_watch_after_build = True
        self._auto_watch_context_key = self._rebuild_context().key

    def cancel_auto_watch_after_build(self) -> None:
        self._auto_watch_after_build = False
        self._auto_watch_context_key = None

    def _on_background_done(self, result: dict[str, Any], context: RebuildContext) -> None:
        if not self._auto_watch_after_build or self._auto_watch_context_key != context.key:
            return
        self.cancel_auto_watch_after_build()
        if not self._context_is_current(context):
            return
        if self.watcher is not None and self.watcher.is_running():
            return
        if not self.can_start_auto_watch(result):
            return
        try:
            self.start_watcher(wait_ready=False)
        except Exception as exc:  # noqa: BLE001 - watcher start is best-effort
            self.last_errors.append(f"auto-watch: {exc}")

    def _rebuild_context(self) -> RebuildContext:
        root, store = self._ready_context()
        assert self.index_root is not None
        return RebuildContext(root, self.index_root, store, self.embedding_indexer, self.ignore_config())

    def _context_is_current(self, context: RebuildContext) -> bool:
        return (
            self.enabled
            and self.root_path is not None
            and self.index_root is not None
            and self.store is context.store
            and (self.root_path.resolve(), self.index_root.resolve()) == context.key
        )

    def _rebuild_now(
        self,
        indexer: BackgroundIndexer | None = None,
        context: RebuildContext | None = None,
    ) -> dict[str, Any]:
        context = context or self._rebuild_context()
        root = context.root
        store = context.store
        start = time.time()
        try:
            metadata = store.get_metadata_map()
            existing = {item["path"]: item for item in store.all_files()} if metadata.get("version") == INDEX_VERSION else {}
        except Exception:
            existing = {}
        if indexer is not None:
            indexer.set_phase(PHASE_SCANNING)
        ignore_config = context.ignore_config
        ignore_patterns = ignore_config.patterns
        privileged_patterns = ignore_config.privileged_patterns
        progress = TreeProgress()
        progress.start(root)
        if self._context_is_current(context):
            self.tree_progress = progress
        children = discover_child_indexes(
            root,
            store.db_path,
            ignore_patterns=ignore_patterns,
        )
        boundary_roots = [Path(child.root) for child in children]
        try:
            scan = SourceScanner(
                str(root),
                extra_excludes=ignore_patterns,
                auto_excludes=ignore_config.auto_patterns,
                privileged_patterns=privileged_patterns,
                existing_records=existing,
                boundary_roots=boundary_roots,
                tree_progress=progress,
                cancelled=indexer.cancelled if indexer is not None else None,
            ).scan()
        finally:
            progress.finish()
        existing.clear()
        ignore_config = ignore_config.with_auto_patterns([exact_path_pattern(path) for path in scan.oversized_paths])
        if self._context_is_current(context):
            self.replace_ignore_config(ignore_config, dirty=True)
        if indexer is not None:
            indexer.check_cancelled()
            indexer.set_phase(PHASE_ANALYZING)
        active_records = annotate_active_sources(root, scan.records)
        records = with_project_quality_findings(resolve_project_callers(active_records))
        scan = replace(scan, records=[])
        del active_records
        if indexer is not None:
            indexer.check_cancelled()
            indexer.set_phase(PHASE_WRITING)
        children_dicts = child_indexes_to_dicts(children)
        total_file_count = len(records) + sum(child.file_count for child in children)
        store.replace_all(
            scan.root,
            records,
            children_dicts,
            config_ignore_metadata(ignore_config, root),
        )
        if self._context_is_current(context):
            self._mark_ignore_config_persisted()
        if self._context_is_current(context):
            self.last_errors = scan.errors[:50]
        if indexer is not None:
            indexer.set_phase(PHASE_EMBEDDING)
        embedding_meta = self._embed_after_full_rebuild(root, store, context.embedding_indexer) if self._context_is_current(context) else None
        end = time.time()
        if indexer is None:
            # Synchronous rebuild has no BackgroundIndexer handle; record its
            # timing so build_timers can still report this build's duration.
            self._last_index_build = {
                "state": STATE_DONE,
                "phase": PHASE_DONE,
                "running": False,
                "elapsed_seconds": round(end - start, 3),
                "started_at": start,
                "finished_at": end,
            }
        result: dict[str, Any] = {
            "status": "indexed",
            "root": scan.root,
            "file_count": len(records),
            "total_file_count": total_file_count,
            "child_index_count": len(children),
            "skipped": scan.skipped,
            "reused": scan.reused,
            "elapsed_seconds": round(end - start, 3),
            "index_path": str(store.db_path),
            "updated_at": iso_time(store.get_metadata_map().get("updated_at")),
            "embedding": embedding_meta,
        }
        # Empty diagnostic lists are noise for LLM callers; include them only
        # when they carry information.
        privileged = scan.privileged_paths
        for key, value in (
            ("auto_ignored_paths", scan.oversized_paths),
            ("oversized_paths", scan.oversized_paths),
            ("privileged_paths", privileged),
        ):
            if value:
                result[key] = value
        if scan.errors:
            result["error_count"] = len(scan.errors)
            result["errors"] = scan.errors[:5]
        return result

    def _index_is_fresh(self) -> bool:
        if self.root_path is None:
            return False
        return can_reuse_index(
            self.store,
            self.root_path,
            service_ignore_fingerprint(self, self.root_path),
        )

    def can_reuse_index_for(self, root: Path) -> bool:
        return can_reuse_index(
            self.store,
            root,
            service_ignore_fingerprint(self, root),
        )

    def can_start_auto_watch(self, result: dict[str, Any] | None) -> bool:
        if self.root_path is None:
            return False
        return can_start_auto_watch_policy(
            self.store,
            self.root_path,
            result,
            service_ignore_fingerprint(self, self.root_path),
        )


def _completed_background_result(indexer: BackgroundIndexer) -> dict[str, Any] | None:
    status = indexer.status()
    result = status.get("last_result")
    return result if isinstance(result, dict) else None
