from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import DEFAULT_WATCH_DEBOUNCE_SECONDS
from .service_state import ServiceBase
from ..indexing.snapshot import snapshot_from_index, take_watch_snapshot, update_watch_snapshot
from ..indexing.updater import IndexUpdater
from ..indexing.store import IndexStore
from ..indexing.watcher import FileEventWatcher, make_event_filter


class ServiceWatcherMixin(ServiceBase):
    """Filesystem-watcher lifecycle.

    Manages the event-driven incremental watcher. Shared state lives in
    ServiceBase; the rebuild entrypoint and the incremental embedding hook
    come from their mixins through the shared MRO.
    """

    def start_watcher(self, debounce_seconds: float = DEFAULT_WATCH_DEBOUNCE_SECONDS, wait_ready: bool = False) -> dict[str, Any]:
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
        ignores = self.runtime_ignore_patterns()
        snapshot = lambda: take_watch_snapshot(root, children(), store.db_path, ignores)
        update_snapshot = lambda previous, paths: update_watch_snapshot(
            root,
            previous,
            paths,
            children(),
            store.db_path,
            ignores,
        )
        previous = snapshot_from_index(root, store.file_headers(), store.child_indexes())
        self.watcher = FileEventWatcher(
            root,
            snapshot,
            update_snapshot,
            self._make_watch_updater(root, store),
            debounce_seconds,
            previous,
            event_filter=make_event_filter(root, store.db_path.parent),
        )
        self.watcher.start(wait_ready=wait_ready)
        return self.watcher_status()

    def sync_index_to_filesystem(self) -> dict[str, Any]:
        root, store = self._ready_context()
        child_indexes = store.child_indexes()
        child_roots = [Path(child["root"]) for child in child_indexes]
        previous = snapshot_from_index(root, store.file_headers(), child_indexes)
        ignores = self.runtime_ignore_patterns()
        current = take_watch_snapshot(root, child_roots, store.db_path, ignores)
        return IndexUpdater(
            root,
            store,
            self.rebuild_sync,
            ignores,
            self.auto_ignore_patterns(),
            self.privileged_ignore_patterns(),
        ).apply(previous, current)

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
        # The watcher runs on its own daemon thread, so a structural rebuild it
        # triggers should complete synchronously there rather than dispatching a
        # second background build the watcher would not wait on.
        updater = IndexUpdater(
            root,
            store,
            self.rebuild_sync,
            self.runtime_ignore_patterns(),
            self.auto_ignore_patterns(),
            self.privileged_ignore_patterns(),
        )

        def apply(previous, current):
            result = updater.apply(previous, current)
            self._embed_after_incremental(root, store, previous, current, result)
            return result

        return apply
