from __future__ import annotations

from .health import watcher_status

from typing import Any

from ..domain.config import DEFAULT_WATCH_DEBOUNCE_SECONDS
from .watch_session import WatchSession
from ..indexing.watcher import FileEventWatcher, make_event_filter


class WatchCoordinator:
    def __init__(self, project, capture_context, run_rebuild, rebuild_sync, after_incremental, maintain_embeddings) -> None:
        self.project = project
        self.capture_context = capture_context
        self.run_rebuild = run_rebuild
        self.rebuild_sync = rebuild_sync
        self.after_incremental = after_incremental
        self.maintain_embeddings = maintain_embeddings

    def start_watcher(self, debounce_seconds: float = DEFAULT_WATCH_DEBOUNCE_SECONDS, wait_ready: bool = False) -> dict[str, Any]:
        with self.project._watcher_lock:
            return self._start_watcher(debounce_seconds, wait_ready)

    def _start_watcher(self, debounce_seconds, wait_ready):
        if not self.project.tasks.accepting:
            return self.watcher_status()
        root, store = self.project._ready_context()
        if debounce_seconds < 0.05:
            raise ValueError("debounce_seconds must be >= 0.05")
        if (
            self.project.watcher
            and self.project.watcher.is_running()
            and self.project.watcher.root.resolve() == root.resolve()
            and self.project.watcher.debounce_seconds == debounce_seconds
        ):
            return self.watcher_status()
        if self.project.watcher:
            self.project.watcher.stop()
        context = self.capture_context()
        session = WatchSession(
            root, store, lambda: self.run_rebuild(context),
            lambda previous, current, result: self.after_incremental(root, store, previous, current, result),
            mount_child_indexes=context.mount_child_indexes,
        )
        watcher = FileEventWatcher(
            root, session.snapshot, session.update_snapshot, session.apply,
            debounce_seconds, session.baseline,
            event_filter=make_event_filter(root, store.db_path.parent, lambda: session.event_rules,
                                          mount_child_indexes=context.mount_child_indexes),
            lease_path=store.db_path.parent / "watcher.lock",
            maintenance=lambda: self._watch_maintenance(root, store, session),
        )
        if self.project.tasks.start_watcher(watcher) and wait_ready:
            watcher.wait_ready()
        return self.watcher_status()

    def _watch_maintenance(self, root, store, session):
        if self.project.enabled and self.project.root_path == root and self.project.store is store:
            metadata = store.get_metadata_map()
            snapshot = self.project.watcher.snapshot if self.project.watcher is not None else None
            if snapshot is not None and snapshot.policy_generation != int(metadata.get("policy_generation", 0)):
                self.project.watcher.request_refresh()
            if (snapshot is not None and session.mount_child_indexes
                    and snapshot.child_indexes != session.children_snapshot()):
                self.project.watcher.request_refresh()
            self.maintain_embeddings()

    def sync_index_to_filesystem(self) -> dict[str, Any]:
        root, store = self.project._ready_context()
        session = WatchSession(root, store, self.rebuild_sync,
                               mount_child_indexes=self.project.mount_child_indexes)
        result = session.apply(session.baseline(), session.snapshot())
        self.project._invalidate_view_cache()
        return result

    def stop_watcher(self, deadline=None) -> dict[str, Any]:
        with self.project._watcher_lock:
            watcher = self.project.watcher
            if watcher:
                watcher.stop(deadline=deadline)
                self.project.tasks.watcher = None
        return self.watcher_status()

    def watcher_status(self) -> dict[str, Any]:
        return watcher_status(self.project)
