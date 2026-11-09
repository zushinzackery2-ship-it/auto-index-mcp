from __future__ import annotations

from ..runtime.maintenance import MaintenanceLease
from ..storage.compaction import compact_database
import time


def clear_index(project, watch, state, delete_file):
    """Maintenance participates in the same writer leases as background work."""
    busy = project.tasks.pause()
    if busy:
        name = "background" if busy == "build" else busy
        return dict(state.status(), status=f"clear-skipped-{name}-running")
    was_watching = project.watcher is not None
    completed = False
    try:
        result = _clear_index(project, watch, state, delete_file)
        completed = result.get("status") != "clear-busy"
        return result
    finally:
        with project.tasks.lock:
            if project.enabled and not project.quiescing:
                project.tasks.open()
        if was_watching and not completed and project.tasks.accepting:
            watch.start_watcher()


def _clear_index(project, watch, state, delete_file):
    store = project._store_context()
    binding = project.bindings.require()
    watch.stop_watcher()
    project._invalidate_view_cache()
    lease = MaintenanceLease(store.db_path.parent, "clear", session=project.session_lease)
    try:
        try:
            lease.__enter__()
        except TimeoutError as exc:
            return dict(state.status(), status="clear-busy", message=str(exc))
        if delete_file:
            project.bindings.retire(time.monotonic() + 10)
            store.delete_file()
            if binding.embedding_store is not None:
                binding.embedding_store.delete_file()
            project.tasks.cancel()
            project.tasks.indexer = None
            if project.root_path is not None:
                project.registry.unregister(store.db_path.parent)
        else:
            store.clear()
            if project.embedding_store is not None:
                project.embedding_store.clear()
                compact_database(project.embedding_store.db_path)
            compact_database(store.db_path)
        project.tree_progress.clear()
        project._invalidate_view_cache()
        return state.status()
    finally:
        lease.__exit__(None, None, None)
        if not project.enabled:
            binding.session.release()
