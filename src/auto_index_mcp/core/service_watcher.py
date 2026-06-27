from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from .config import DEFAULT_WATCH_DEBOUNCE_SECONDS
from ..embedding.backend import create_embedder
from ..embedding.indexer import SymbolEmbedder
from ..indexing.snapshot import snapshot_from_index, take_watch_snapshot, update_watch_snapshot
from ..indexing.updater import IndexUpdater
from ..indexing.store import IndexStore
from ..indexing.watcher import FileEventWatcher


class ServiceWatcherMixin:
    """Filesystem-watcher lifecycle and embedding upkeep.

    Manages the event-driven incremental watcher plus the full and incremental
    symbol-embedding passes. Shared state and the rebuild entrypoint are
    provided by AutoIndexService at runtime.
    """

    if TYPE_CHECKING:
        root_path: Path | None
        store: IndexStore | None
        watcher: FileEventWatcher | None
        embedding_indexer: SymbolEmbedder | None
        last_errors: list[str]

        def _ready_context(self) -> tuple[Path, IndexStore]: ...
        def rebuild_sync(self, reuse_if_fresh: bool = ...) -> dict[str, Any]: ...

    def start_watcher(self, debounce_seconds: float = DEFAULT_WATCH_DEBOUNCE_SECONDS, wait_ready: bool = True) -> dict[str, Any]:
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
        snapshot = lambda: take_watch_snapshot(root, children(), store.db_path)
        update_snapshot = lambda previous, paths: update_watch_snapshot(root, previous, paths, children(), store.db_path)
        previous = snapshot_from_index(root, store.file_headers(), store.child_indexes())
        self.watcher = FileEventWatcher(
            root,
            snapshot,
            update_snapshot,
            self._make_watch_updater(root, store),
            debounce_seconds,
            previous,
        )
        self.watcher.start(wait_ready=wait_ready)
        return self.watcher_status()

    def sync_index_to_filesystem(self) -> dict[str, Any]:
        root, store = self._ready_context()
        child_indexes = store.child_indexes()
        child_roots = [Path(child["root"]) for child in child_indexes]
        previous = snapshot_from_index(root, store.file_headers(), child_indexes)
        current = take_watch_snapshot(root, child_roots, store.db_path)
        return IndexUpdater(root, store, self.rebuild_sync).apply(previous, current)

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
        updater = IndexUpdater(root, store, self.rebuild_sync)

        def apply(previous, current):
            result = updater.apply(previous, current)
            self._embed_after_incremental(root, store, previous, current, result)
            return result

        return apply

    def _refresh_embedder(self) -> None:
        store = self.store
        if store is None:
            self.embedding_indexer = None
            return
        backend = create_embedder()
        self.embedding_indexer = SymbolEmbedder(backend, store) if backend is not None else None

    def _embed_after_full_rebuild(
        self,
        root: Path,
        store: IndexStore | None = None,
        indexer: SymbolEmbedder | None = None,
    ) -> dict[str, Any] | None:
        indexer = indexer or self.embedding_indexer
        store = store or self.store
        if indexer is None or store is None:
            return None
        try:
            return indexer.embed_project(root, store.all_symbols())
        except Exception as exc:
            self.last_errors.append(f"embedding-rebuild: {exc}")
            return {"error": str(exc)}

    def _embed_after_incremental(self, root: Path, store: IndexStore, previous, current, result: dict[str, Any]) -> None:
        indexer = self.embedding_indexer
        if indexer is None:
            return
        status = result.get("status")
        if status in ("structural-rebuild", "indexed", "build-lock-timeout", "shared-index-current"):
            return
        if status != "incremental":
            return
        added, deleted, modified = current.changed_files(previous)
        changed = set(added) | set(modified)
        if changed:
            symbols = [s for s in store.all_symbols() if s["file_path"] in changed]
            grouped: dict[str, list[dict[str, Any]]] = {}
            for symbol in symbols:
                grouped.setdefault(symbol["file_path"], []).append(symbol)
            if grouped:
                try:
                    indexer.embed_files(root, grouped)
                except Exception as exc:
                    self.last_errors.append(f"embedding-incremental: {exc}")
        if deleted:
            try:
                with store.connect() as conn:
                    for path in deleted:
                        indexer.store.delete_file(conn, path)
            except Exception as exc:
                self.last_errors.append(f"embedding-delete: {exc}")
