from __future__ import annotations

from ..runtime.maintenance import MaintenanceLease


def clear_index(project, watch, state, delete_file):
    """Maintenance participates in the same writer leases as background work."""
    for worker, name in ((project.background, "background"), (project.embedding_background, "embedding")):
        if worker is not None and worker.is_running():
            return dict(state.status(), status=f"clear-skipped-{name}-running")
    store = project._store_context()
    was_watching = project.watcher is not None
    watch.stop_watcher()
    lease = MaintenanceLease(store.db_path.parent, "clear")
    try:
        try:
            lease.__enter__()
        except TimeoutError as exc:
            return dict(state.status(), status="clear-busy", message=str(exc))
        if delete_file:
            store.delete_file()
            if project.embedding_store is not None:
                project.embedding_store.delete_file()
            project.store = None
            project.embedding_store = None
            project.embedding_indexer = None
            project.enabled = False
            if project.root_path is not None:
                project.registry.unregister(store.db_path.parent)
        else:
            store.clear()
            if project.embedding_store is not None:
                project.embedding_store.clear()
        project.tree_progress.clear()
        project._invalidate_view_cache()
        was_watching = False
        return state.status()
    finally:
        lease.__exit__(None, None, None)
        if was_watching and project.enabled:
            watch.start_watcher()
