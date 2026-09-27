from __future__ import annotations

from typing import Any

from ..runtime.background import BackgroundIndexer, PHASE_EMBEDDING
from ..embedding.backend import create_embedder, resolve_embedding_model_path
from ..storage.completion import is_complete, mark_complete
from ..storage.embeddings import EmbeddingStore
from ..embedding.indexer import SymbolEmbedder
from ..runtime.leases import BuildLock
from ..storage.generation import revision as source_revision
from ..workspace.semantic import vector_sources, workspace_complete
from ..workspace.view import WorkspaceView
from ..workspace.semantic import describe_sources


class EmbeddingCoordinator:
    def __init__(self, project) -> None:
        self.project = project

    def ensure_embedding_background(self) -> dict[str, Any]:
        root, store = self.project._ready_context()
        if not self.project.semantic_enabled:
            return dict(status="embedding-disabled")
        with self.project._embedding_lock:
            if not self.project.enabled:
                return dict(status="embedding-cancelled")
            existing = self.project.embedding_background
            if existing is not None and existing.is_running():
                return existing.status()
            indexer = self.project.embedding_indexer
            if indexer is not None and self._vectors_current(store, indexer):
                return dict(status="embedding-ready", model=indexer.backend.name,
                            vector_count=sum(item["vector_count"] for item in describe_sources(WorkspaceView(store), root, indexer)))
            embedding_store = self.project.embedding_store
            worker = BackgroundIndexer(
                lambda background: self._load_and_embed_project(background, root, store, embedding_store),
                on_done=lambda result: self._embedding_done(root, store),
                operation="embedding", root=root, index_root=store.db_path.parent,
            )
            self.project.embedding_background = worker
            worker.start()
        return worker.status()

    def _embedding_done(self, root, store):
        if not self.project.enabled or self.project.store is not store or self.project.root_path != root:
            return
        indexer = self.project.embedding_indexer
        if indexer is not None and not self._vectors_current(store, indexer):
            # The last request can arrive after a pass's final revision check.
            result = self.project.embedding_background.status().get("last_result") or dict()
            if result.get("status") == "embedded":
                self.ensure_embedding_background()

    def _vectors_current(self, store, indexer):
        return workspace_complete(WorkspaceView(store), self.project.root_path, indexer)

    def _refresh_embedder(self) -> None:
        self.project.embedding_indexer = self._create_embedding_indexer()

    def _create_embedding_indexer(self, embedding_store: EmbeddingStore | None = None) -> SymbolEmbedder | None:
        store = embedding_store if embedding_store is not None else self.project.embedding_store
        if not self.project.semantic_enabled or store is None:
            return None
        backend = create_embedder()
        if backend is None:
            return None
        indexer = SymbolEmbedder(backend, store)
        indexer.progress = self.project.embedding_progress
        return indexer

    def _load_and_embed_project(self, background, root, store, embedding_store=None):
        background.set_phase(PHASE_EMBEDDING)
        indexer = self._create_embedding_indexer(embedding_store)
        if indexer is None:
            return dict(status="embedding-unavailable", model=None,
                        error="embedding model unavailable; install semantic dependencies and configure the model")
        if self.project.store is store and self.project.root_path == root and self.project.enabled:
            self.project.embedding_indexer = indexer
        indexer.check_cancelled = background.check_cancelled
        results = []
        for prefix, source_root, source_store, vectors in vector_sources(WorkspaceView(store), root, indexer.conn_provider):
            background.check_cancelled()
            vectors.initialize()
            source_indexer = indexer if not prefix else SymbolEmbedder(indexer.backend, vectors)
            source_indexer.check_cancelled = background.check_cancelled
            source_indexer.progress = self.project.embedding_progress
            result = self._embed_project_exclusive(source_root, source_store, source_indexer, 0.0)
            results.append(dict(result, path=prefix))
        waiting = any(item.get("status") in ("embedding-in-other-process", "embedding-waiting-for-index") for item in results)
        return dict(status="embedding-in-other-process" if waiting else "embedded", model=indexer.backend.name,
                    build_lock=next((item["build_lock"] for item in results if item.get("build_lock")), None),
                    sources=results, embedded=sum(item.get("embedded", 0) for item in results),
                    reused=sum(item.get("reused", 0) for item in results),
                    vector_count=sum(item.get("vector_count", 0) for item in results))

    def _embed_after_full_rebuild(self, root, store=None, indexer=None):
        if not self.project.semantic_enabled:
            return dict(status="embedding-disabled", model=None)
        store = store or self.project.store
        if store is None or self.project.store is not store or self.project.root_path != root or not self.project.enabled:
            return None
        if not self.project.semantic_auto_start and self.project.embedding_indexer is None:
            return dict(status="embedding-on-demand", model=None)
        self.ensure_embedding_background()
        return dict(status="embedding-in-background", model=_embedding_model_name(indexer or self.project.embedding_indexer))

    def _embed_project_exclusive(self, root, store, indexer, wait_seconds):
        lock = BuildLock(indexer.conn_provider.db_path.parent / "embeddings.build.lock")
        if not lock.acquire(wait_seconds):
            return dict(status="embedding-in-other-process", model=indexer.backend.name, build_lock=lock.state_info())
        try:
            with indexer.conn_provider.connect() as conn:
                conn.execute("INSERT OR IGNORE INTO metadata(key,value) VALUES ('requested','true')")
            while self.project.enabled:
                indexer.check_cancelled()
                revision = source_revision(store.get_metadata_map())
                if revision is None:
                    return dict(status="embedding-waiting-for-index", model=indexer.backend.name)
                if is_complete(indexer.conn_provider, indexer.model_key, revision):
                    return dict(status="embedding-ready", model=indexer.backend.name, vector_count=indexer.count())
                result = indexer.embed_project(root, store.iter_symbols())
                # A concurrent index publication invalidates this pass. Reuse
                # its per-file commits and converge on the newest revision.
                if source_revision(store.get_metadata_map()) == revision:
                    mark_complete(indexer.conn_provider, indexer.model_key, revision)
                    result["status"] = "embedded"
                    result["vector_count"] = indexer.count()
                    return result
            return dict(status="embedding-cancelled")
        finally:
            lock.release()

    def _embed_after_incremental(self, root, store, previous, current, result):
        if self.project.enabled and self.project.store is store and result.get("status") == "incremental":
            self._maintain_embeddings()

    def _maintain_embeddings(self):
        if not self.project.enabled or not self.project.semantic_enabled or self.project.embedding_store is None:
            return
        if self.project.embedding_background is not None and self.project.embedding_background.is_running():
            return
        active = self.project.semantic_auto_start or self.project.embedding_indexer is not None
        if not active:
            with self.project.embedding_store.read_connect() as conn:
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
