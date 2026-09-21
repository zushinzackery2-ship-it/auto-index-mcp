from __future__ import annotations

from ..indexing.build_lock import BuildLock


def clear_index(service, delete_file):
    """Maintenance participates in the same writer leases as background work."""
    for worker, name in ((service.background, "background"), (service.embedding_background, "embedding")):
        if worker is not None and worker.is_running():
            return dict(service.status(), status=f"clear-skipped-{name}-running")
    store = service._store_context()
    was_watching = service.watcher is not None
    service.stop_watcher()
    locks = [BuildLock(store.db_path.parent / name) for name in
             ("watcher.lock", "index.build.lock", "embeddings.build.lock")]
    try:
        for lock in locks:
            if not lock.try_acquire():
                return dict(service.status(), status="clear-busy", build_lock=lock.state_info(),
                            message="another process is maintaining this index; retry when it is idle")
        if delete_file:
            store.delete_file()
            if service.embedding_store is not None:
                service.embedding_store.delete_file()
            service.store = None
            service.embedding_store = None
            service.embedding_indexer = None
            service.enabled = False
            if service.root_path is not None:
                service.registry.unregister(service.root_path)
        else:
            store.clear()
            if service.embedding_store is not None:
                service.embedding_store.clear()
        service.tree_progress.clear()
        service._invalidate_view_cache()
        was_watching = False
        return service.status()
    finally:
        for lock in reversed(locks):
            lock.release()
        if was_watching and service.enabled:
            service.start_watcher()
