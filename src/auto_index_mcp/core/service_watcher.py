from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from .background_indexer import BackgroundIndexer, PHASE_EMBEDDING
from .config import DEFAULT_WATCH_DEBOUNCE_SECONDS
from ..embedding.backend import create_embedder, resolve_embedding_model_path
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
        embedding_background: BackgroundIndexer | None
        last_errors: list[str]

        def _ready_context(self) -> tuple[Path, IndexStore]: ...
        def runtime_ignore_patterns(self) -> list[str]: ...
        def auto_ignore_patterns(self) -> list[str]: ...
        def privileged_ignore_patterns(self) -> list[str]: ...
        def rebuild_sync(self, reuse_if_fresh: bool = ...) -> dict[str, Any]: ...

    def ensure_embedding_background(self) -> dict[str, Any]:
        root, store = self._ready_context()
        existing = self.embedding_background
        if existing is not None and existing.is_running():
            return existing.status()
        if self.embedding_indexer is not None:
            try:
                if self.embedding_indexer.count(store) > 0:
                    return {"state": "ready", "model": self.embedding_indexer.backend.name}
            except Exception:
                pass
        worker = BackgroundIndexer(
            lambda background: self._load_and_embed_project(background, root, store)
        )
        self.embedding_background = worker
        worker.start()
        return worker.status()

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

    def _refresh_embedder(self) -> None:
        store = self.store
        if store is None:
            self.embedding_indexer = None
            return
        self.embedding_indexer = self._create_embedding_indexer(store)

    def _create_embedding_indexer(self, store: IndexStore) -> SymbolEmbedder | None:
        backend = create_embedder()
        return SymbolEmbedder(backend, store) if backend is not None else None

    def _load_and_embed_project(
        self,
        background: BackgroundIndexer,
        root: Path,
        store: IndexStore,
    ) -> dict[str, Any]:
        background.set_phase(PHASE_EMBEDDING)
        indexer = self._create_embedding_indexer(store)
        if indexer is None:
            return {
                "status": "embedding-unavailable",
                "model": None,
                "error": (
                    "embedding model unavailable; install semantic dependencies "
                    "and keep models/minilm-onnx, or set AUTO_INDEX_EMBEDDING_MODEL"
                ),
            }
        if self.root_path is not None and self.root_path.resolve() == root.resolve() and self.store is store:
            self.embedding_indexer = indexer
        try:
            count = indexer.count(store)
            if count > 0:
                return {
                    "status": "embedding-ready",
                    "model": indexer.backend.name,
                    "vector_count": count,
                }
            result = indexer.embed_project(root, store.all_symbols())
            result["status"] = "embedded"
            return result
        except Exception as exc:
            self.last_errors.append(f"embedding-load: {exc}")
            raise

    def _embed_after_full_rebuild(
        self,
        root: Path,
        store: IndexStore | None = None,
        indexer: SymbolEmbedder | None = None,
    ) -> dict[str, Any] | None:
        indexer = indexer or self.embedding_indexer
        store = store or self.store
        if store is None:
            return None
        existing = self.embedding_background
        if existing is not None and existing.is_running():
            model = _embedding_model_name(indexer)
            return {"status": "embedding-in-background", "model": model}
        if indexer is None:
            worker = BackgroundIndexer(
                lambda background: self._load_and_embed_project(background, root, store)
            )
            model = _embedding_model_name(None)
        else:
            worker = BackgroundIndexer(
                lambda background: self._run_full_embedding(background, root, store, indexer)
            )
            model = _embedding_model_name(indexer)
        self.embedding_background = worker
        worker.start()
        return {"status": "embedding-in-background", "model": model}

    def _run_full_embedding(
        self,
        background: BackgroundIndexer,
        root: Path,
        store: IndexStore,
        indexer: SymbolEmbedder,
    ) -> dict[str, Any]:
        background.set_phase(PHASE_EMBEDDING)
        try:
            return indexer.embed_project(root, store.all_symbols())
        except Exception as exc:
            self.last_errors.append(f"embedding-rebuild: {exc}")
            raise

    def _embed_after_incremental(self, root: Path, store: IndexStore, previous, current, result: dict[str, Any]) -> None:
        indexer = self.embedding_indexer
        if indexer is None:
            return
        status = result.get("status")
        if status in ("structural-rebuild", "indexed", "indexing-in-other-process", "shared-index-current"):
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


def _embedding_model_name(indexer: SymbolEmbedder | None) -> str | None:
    if indexer is not None:
        return indexer.backend.name
    model_path = resolve_embedding_model_path()
    return model_path.name if model_path is not None else None
