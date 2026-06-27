from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .config import INDEX_VERSION
from .background_indexer import (
    BackgroundIndexer,
    PHASE_ANALYZING,
    PHASE_EMBEDDING,
    PHASE_SCANNING,
    PHASE_WRITING,
)
from .index_policy import can_reuse_index, can_start_auto_watch
from .ignore_rules import ignore_fingerprint
from .quality_dangling import with_project_quality_findings
from .rebuild_context import RebuildContext
from ..indexing.analysis import resolve_project_callers
from ..indexing.active_sources import annotate_active_sources
from ..indexing.scanner import SourceScanner
from ..indexing.build_lock import BuildLock
from ..indexing.store import IndexStore
from ..embedding.indexer import SymbolEmbedder
from ..workspace.discovery import child_indexes_to_dicts, discover_child_indexes


class ServiceRebuildMixin:
    """Full-tree rebuild orchestration.

    Owns background dispatch, cross-process build-lock handling, per-phase
    progress reporting, and the index-state envelopes that keep search
    responses honest while a build is in flight. Concrete state
    (store/root_path/background/...) and the watcher/embedding hooks are
    provided by AutoIndexService at runtime.
    """

    if TYPE_CHECKING:
        index_root: Path | None
        root_path: Path | None
        store: IndexStore | None
        background: BackgroundIndexer | None
        embedding_indexer: SymbolEmbedder | None
        watcher: Any
        enabled: bool
        last_errors: list[str]
        _auto_watch_after_build: bool
        _auto_watch_context_key: tuple[Path, Path] | None
        _background_context_key: tuple[Path, Path] | None

        def _ready_context(self) -> tuple[Path, IndexStore]: ...
        def status(self) -> dict[str, Any]: ...
        def _background_status(self) -> dict[str, Any]: ...
        def runtime_ignore_patterns(self) -> list[str]: ...
        def start_watcher(self, debounce_seconds: float = ..., wait_ready: bool = ...) -> dict[str, Any]: ...
        def _embed_after_full_rebuild(
            self,
            root: Path,
            store: IndexStore | None = ...,
            indexer: SymbolEmbedder | None = ...,
        ) -> dict[str, Any] | None: ...
        def _create_embedding_indexer(self, store: IndexStore) -> SymbolEmbedder | None: ...

    def rebuild(self, reuse_if_fresh: bool = False) -> dict[str, Any]:
        self._ready_context()
        assert self.index_root is not None
        # The fresh check stays synchronous and lock-free: it is a fast metadata
        # probe and decides whether we can skip the heavy scan entirely.
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
        """Acquire the cross-process BuildLock, then run the rebuild.

        Shared by the synchronous path and the background worker so multi-process
        contention is handled identically: if another process holds the lock,
        report that an external build is in flight instead of blocking or racing
        a duplicate scan.
        """
        lock = BuildLock(context.index_root / "index.build.lock")
        acquired = lock.try_acquire()
        try:
            if not acquired:
                result = self.status()
                result["status"] = "indexing-in-other-process"
                result["rebuild"] = False
                result["message"] = "another auto-index process is still rebuilding this project"
                return result
            return self._rebuild_now(indexer, context)
        finally:
            lock.release()

    def _start_background_rebuild(self) -> dict[str, Any]:
        """Dispatch a full-tree rebuild to the background indexer and return immediately.

        If a background rebuild is already running for this service, the call is
        idempotent and returns the in-progress status. This avoids duplicate
        scans when lifecycle code calls both enable_reusing_index and rebuild.
        """
        context = self._rebuild_context()
        existing = self.background
        if existing is not None and existing.is_running() and self._background_context_key == context.key:
            return self._background_status()
        indexer = BackgroundIndexer(
            lambda worker: self._run_rebuild_locked(worker, context),
            on_done=lambda result: self._on_background_done(result, context),
        )
        self.background = indexer
        self._background_context_key = context.key
        indexer.start(delay_seconds=0.25)
        return self._background_status()

    def _run_rebuild_locked(self, indexer: BackgroundIndexer, context: RebuildContext) -> dict[str, Any]:
        """Worker entrypoint for the background indexer.

        Runs on the daemon thread so the BuildLock wait and the heavy scan never
        block the MCP request thread.
        """
        return self._rebuild_with_lock(context, indexer)

    def request_auto_watch_after_build(self) -> None:
        """Start the watcher automatically once the current background build ends.

        enable's auto_watch path cannot start a watcher while the first build is
        still running (the index is not reusable yet), so the intent is recorded
        here and honoured by the completion hook.
        """
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
        return RebuildContext(root, self.index_root, store, self.embedding_indexer)

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
        ignore_patterns = self.runtime_ignore_patterns()
        children = discover_child_indexes(
            root,
            store.db_path,
            ignore_patterns=ignore_patterns,
        )
        boundary_roots = [Path(child.root) for child in children]
        scan = SourceScanner(
            str(root),
            extra_excludes=ignore_patterns,
            existing_records=existing,
            boundary_roots=boundary_roots,
        ).scan()
        if indexer is not None:
            indexer.set_phase(PHASE_ANALYZING)
        active_records = annotate_active_sources(root, scan.records)
        records = with_project_quality_findings(resolve_project_callers(active_records))
        if indexer is not None:
            indexer.set_phase(PHASE_WRITING)
        children_dicts = child_indexes_to_dicts(children)
        total_file_count = len(records) + sum(child.file_count for child in children)
        store.replace_all(
            scan.root,
            records,
            children_dicts,
            {"ignore_fingerprint": ignore_fingerprint(root, ignore_patterns)},
        )
        if self._context_is_current(context):
            self.last_errors = scan.errors[:50]
        if indexer is not None:
            indexer.set_phase(PHASE_EMBEDDING)
        embedding_indexer = context.embedding_indexer or self._create_embedding_indexer(store)
        if self._context_is_current(context):
            self.embedding_indexer = embedding_indexer
        embedding_meta = self._embed_after_full_rebuild(root, store, embedding_indexer)
        return {
            "status": "indexed",
            "root": scan.root,
            "file_count": len(records),
            "total_file_count": total_file_count,
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
        if self.root_path is None:
            return False
        return can_reuse_index(
            self.store,
            self.root_path,
            ignore_fingerprint(self.root_path, self.runtime_ignore_patterns()),
        )

    def can_reuse_index_for(self, root: Path) -> bool:
        return can_reuse_index(
            self.store,
            root,
            ignore_fingerprint(root, self.runtime_ignore_patterns()),
        )

    def can_start_auto_watch(self, result: dict[str, Any] | None) -> bool:
        if self.root_path is None:
            return False
        return can_start_auto_watch(
            self.store,
            self.root_path,
            result,
            ignore_fingerprint(self.root_path, self.runtime_ignore_patterns()),
        )
