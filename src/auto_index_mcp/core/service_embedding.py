from __future__ import annotations

from typing import Any

from .background_indexer import BackgroundIndexer, PHASE_EMBEDDING
from .service_state import ServiceBase
from ..embedding.backend import create_embedder, resolve_embedding_model_path
from ..embedding.completion import is_complete, mark_complete
from ..embedding.embedding_store import EmbeddingStore
from ..embedding.indexer import SymbolEmbedder
from ..indexing.build_lock import BuildLock

class ServiceEmbeddingMixin(ServiceBase):
    """Versioned, coalesced embedding work under a process-wide build lease."""

    def ensure_embedding_background(self) -> dict[str, Any]:
        root, store = self._ready_context()
        if not self.semantic_enabled:
            return dict(status="embedding-disabled")
        with self._embedding_lock:
            existing = self.embedding_background
            if existing is not None and existing.is_running():
                return existing.status()
            indexer = self.embedding_indexer
            if indexer is not None and self._vectors_current(store, indexer):
                return dict(status="embedding-ready", model=indexer.backend.name, vector_count=indexer.count())
            embedding_store = self.embedding_store
            worker = BackgroundIndexer(
                lambda background: self._load_and_embed_project(background, root, store, embedding_store),
                on_done=lambda result: self._embedding_done(root, store),
            )
            self.embedding_background = worker
            worker.start()
        return worker.status()

    def _embedding_done(self, root, store):
        if not self.enabled or self.store is not store or self.root_path != root:
            return
        indexer = self.embedding_indexer
        if indexer is not None and not self._vectors_current(store, indexer):
            # The last request can arrive after a pass's final revision check.
            result = self.embedding_background.status().get("last_result") or dict()
            if result.get("status") == "embedded":
                self.ensure_embedding_background()

    def _vectors_current(self, store, indexer):
        return is_complete(indexer.conn_provider, indexer.model_key, store.get_metadata_map().get("updated_at"))

    def _refresh_embedder(self) -> None:
        self.embedding_indexer = self._create_embedding_indexer()

    def _create_embedding_indexer(self, embedding_store: EmbeddingStore | None = None) -> SymbolEmbedder | None:
        store = embedding_store if embedding_store is not None else self.embedding_store
        if not self.semantic_enabled or store is None:
            return None
        backend = create_embedder()
        if backend is None:
            return None
        indexer = SymbolEmbedder(backend, store)
        indexer.progress = self.embedding_progress
        return indexer

    def _load_and_embed_project(self, background, root, store, embedding_store=None):
        background.set_phase(PHASE_EMBEDDING)
        indexer = self._create_embedding_indexer(embedding_store)
        if indexer is None:
            return dict(status="embedding-unavailable", model=None,
                        error="embedding model unavailable; install semantic dependencies and configure the model")
        if self.store is store and self.root_path == root and self.enabled:
            self.embedding_indexer = indexer
        indexer.check_cancelled = background.check_cancelled
        return self._embed_project_exclusive(root, store, indexer, 0.0, True)

    def _embed_after_full_rebuild(self, root, store=None, indexer=None):
        if not self.semantic_enabled:
            return dict(status="embedding-disabled", model=None)
        store = store or self.store
        if store is None or self.store is not store or self.root_path != root or not self.enabled:
            return None
        if not self.semantic_auto_start and self.embedding_indexer is None:
            return dict(status="embedding-on-demand", model=None)
        self.ensure_embedding_background()
        return dict(status="embedding-in-background", model=_embedding_model_name(indexer or self.embedding_indexer))

    def _embed_project_exclusive(self, root, store, indexer, wait_seconds, skip_if_populated):
        lock = BuildLock(indexer.conn_provider.db_path.parent / "embeddings.build.lock")
        if not lock.acquire(wait_seconds):
            return dict(status="embedding-in-other-process", model=indexer.backend.name, build_lock=lock.state_info())
        try:
            with indexer.conn_provider.connect() as conn:
                conn.execute("INSERT OR IGNORE INTO metadata(key,value) VALUES ('requested','true')")
            while self.enabled and self.store is store and self.root_path == root:
                indexer.check_cancelled()
                revision = store.get_metadata_map().get("updated_at")
                if is_complete(indexer.conn_provider, indexer.model_key, revision):
                    return dict(status="embedding-ready", model=indexer.backend.name, vector_count=indexer.count())
                result = indexer.embed_project(root, store.iter_symbols())
                # A concurrent index publication invalidates this pass. Reuse
                # its per-file commits and converge on the newest revision.
                if store.get_metadata_map().get("updated_at") == revision:
                    mark_complete(indexer.conn_provider, indexer.model_key, revision)
                    result["status"] = "embedded"
                    return result
            return dict(status="embedding-cancelled")
        finally:
            lock.release()

    def _embed_after_incremental(self, root, store, previous, current, result):
        if self.enabled and self.store is store and result.get("status") == "incremental":
            self._maintain_embeddings()

    def _maintain_embeddings(self):
        if not self.enabled or not self.semantic_enabled or self.embedding_store is None:
            return
        if self.embedding_background is not None and self.embedding_background.is_running():
            return
        active = self.semantic_auto_start or self.embedding_indexer is not None
        if not active:
            with self.embedding_store.read_connect() as conn:
                active = conn.execute("SELECT 1 FROM metadata WHERE key='requested'").fetchone() is not None
                if not active:
                    active = conn.execute("SELECT 1 FROM symbol_embeddings LIMIT 1").fetchone() is not None
        if active:
            self.ensure_embedding_background()


def _embedding_model_name(indexer: SymbolEmbedder | None) -> str | None:
    if indexer is not None:
        return indexer.backend.name
    model_path = resolve_embedding_model_path()
    return model_path.name if model_path is not None else None
