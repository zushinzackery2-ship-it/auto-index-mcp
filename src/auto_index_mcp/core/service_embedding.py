from __future__ import annotations

from pathlib import Path
from typing import Any

from .background_indexer import BackgroundIndexer, PHASE_EMBEDDING
from .service_state import ServiceBase
from ..embedding.backend import create_embedder, resolve_embedding_model_path
from ..embedding.embedding_store import EmbeddingStore
from ..embedding.indexer import SymbolEmbedder
from ..indexing.build_lock import BuildLock
from ..indexing.store import IndexStore

# Post-rebuild embedding passes must land even when another process's full
# pass currently holds the embeddings lock (that pass may have read the
# pre-rebuild symbol set), so the rebuild worker waits for its turn. The cap
# is an emergency valve only: a dead holder is reclaimed by PID liveness and
# a live one keeps the lock heartbeat fresh while it works.
EMBEDDING_LOCK_WAIT_SECONDS = 3600.0


class ServiceEmbeddingMixin(ServiceBase):
    """Full and incremental symbol-embedding upkeep.

    Owns embedder construction plus the background embedding passes, all
    serialized across processes through the embeddings BuildLock so N agents
    sharing one project never run N identical full ONNX passes. Shared state
    lives in ServiceBase; the watcher mixin calls into the incremental hook
    through the shared MRO.
    """

    def ensure_embedding_background(self) -> dict[str, Any]:
        root, store = self._ready_context()
        if not self.semantic_enabled:
            return {"status": "embedding-disabled"}
        embedding_store = self.embedding_store
        if self.embedding_indexer is not None:
            try:
                if self.embedding_indexer.count() > 0:
                    return {"state": "ready", "model": self.embedding_indexer.backend.name}
            except Exception:
                pass
        with self._embedding_lock:
            existing = self.embedding_background
            if existing is not None and existing.is_running():
                return existing.status()
            worker = BackgroundIndexer(
                lambda background: self._load_and_embed_project(background, root, store, embedding_store)
            )
            self.embedding_background = worker
        worker.start()
        return worker.status()

    def _refresh_embedder(self) -> None:
        if self.store is None or self.embedding_store is None:
            self.embedding_indexer = None
            return
        self.embedding_indexer = self._create_embedding_indexer()

    def _create_embedding_indexer(self, embedding_store: EmbeddingStore | None = None) -> SymbolEmbedder | None:
        if not self.semantic_enabled:
            return None
        store = embedding_store if embedding_store is not None else self.embedding_store
        if store is None:
            return None
        backend = create_embedder()
        if backend is None:
            return None
        indexer = SymbolEmbedder(backend, store)
        indexer.progress = self.embedding_progress
        return indexer

    def _load_and_embed_project(
        self,
        background: BackgroundIndexer,
        root: Path,
        store: IndexStore,
        embedding_store: EmbeddingStore | None = None,
    ) -> dict[str, Any]:
        background.set_phase(PHASE_EMBEDDING)
        indexer = self._create_embedding_indexer(embedding_store)
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
            count = indexer.count()
            if count > 0:
                return {
                    "status": "embedding-ready",
                    "model": indexer.backend.name,
                    "vector_count": count,
                }
            # Ensure path: vectors are derived data over a reused index, so a
            # concurrent process's identical pass is as good as ours - do not
            # wait, just report it and pick the vectors up through count().
            result = self._embed_project_exclusive(
                root,
                store,
                indexer,
                wait_seconds=0.0,
                skip_if_populated=True,
            )
            if "status" not in result:
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
        if not self.semantic_enabled:
            return {"status": "embedding-disabled", "model": None}
        indexer = indexer or self.embedding_indexer
        store = store or self.store
        if store is None:
            return None
        embedding_store = self.embedding_store
        with self._embedding_lock:
            existing = self.embedding_background
            if existing is not None and existing.is_running():
                model = _embedding_model_name(indexer)
                return {"status": "embedding-in-background", "model": model}
            if indexer is None:
                worker = BackgroundIndexer(
                    lambda background: self._load_and_embed_project(background, root, store, embedding_store)
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
            return self._embed_project_exclusive(
                root,
                store,
                indexer,
                wait_seconds=EMBEDDING_LOCK_WAIT_SECONDS,
                skip_if_populated=False,
            )
        except Exception as exc:
            self.last_errors.append(f"embedding-rebuild: {exc}")
            raise

    def _embed_project_exclusive(
        self,
        root: Path,
        store: IndexStore,
        indexer: SymbolEmbedder,
        wait_seconds: float,
        skip_if_populated: bool,
    ) -> dict[str, Any]:
        """Run one full embedding pass under the cross-process embeddings lock.

        Several MCP processes pointing at the same project would otherwise all
        start an identical full pass over one embeddings.db; the lock lets the
        first one work while the rest either skip (ensure path) or queue up
        (post-rebuild path, via ``wait_seconds``). ``skip_if_populated``
        re-checks count() inside the lock so a pass that finished while we
        waited is not repeated; rebuild callers keep it False because changed
        symbols must re-embed even when vectors already exist.
        """
        lock = BuildLock(indexer.conn_provider.db_path.parent / "embeddings.build.lock")
        if not lock.acquire(wait_seconds):
            return {
                "status": "embedding-in-other-process",
                "model": indexer.backend.name,
                "build_lock": lock.state_info(),
            }
        try:
            if skip_if_populated:
                count = indexer.count()
                if count > 0:
                    return {
                        "status": "embedding-ready",
                        "model": indexer.backend.name,
                        "vector_count": count,
                    }
            return indexer.embed_project(root, store.all_symbols())
        finally:
            lock.release()

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
        changed = sorted(set(added) | set(modified))
        if changed:
            grouped: dict[str, list[dict[str, Any]]] = {}
            for symbol in store.symbols_for_files(changed):
                grouped.setdefault(symbol["file_path"], []).append(symbol)
            if grouped:
                try:
                    indexer.embed_files(root, grouped)
                except Exception as exc:
                    self.last_errors.append(f"embedding-incremental: {exc}")
        if deleted:
            try:
                indexer.delete_files(deleted)
            except Exception as exc:
                self.last_errors.append(f"embedding-delete: {exc}")


def _embedding_model_name(indexer: SymbolEmbedder | None) -> str | None:
    if indexer is not None:
        return indexer.backend.name
    model_path = resolve_embedding_model_path()
    return model_path.name if model_path is not None else None
