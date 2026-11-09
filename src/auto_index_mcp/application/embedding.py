from __future__ import annotations

from typing import Any
import logging

from ..runtime.background import BackgroundIndexer, PHASE_EMBEDDING
from ..embedding.backend import create_embedder, resolve_embedding_model_path
from ..storage.completion import begin_pass, checkpoint_revision, is_complete, mark_complete
from ..storage.embeddings import EmbeddingStore
from ..embedding.indexer import SymbolEmbedder
from ..runtime.leases import BuildLock
from ..storage.generation import revision as source_revision
from ..workspace.semantic import vector_sources, workspace_complete
from ..workspace.semantic import describe_sources
from ..domain.errors import ServiceError
from ..storage.namespace import retain_namespaces, retention_limits

logger = logging.getLogger(__name__)


class EmbeddingCoordinator:
    def __init__(self, project) -> None:
        self.project = project

    def ensure_embedding_background(self) -> dict[str, Any]:
        if not self.project.tasks.accepting:
            return dict(status="embedding-cancelled")
        root, store = self.project._ready_context()
        binding = self.project.bindings.require()
        if not self.project.semantic_enabled:
            return dict(status="embedding-disabled")
        with self.project._embedding_lock:
            if not self.project.tasks.accepting:
                return dict(status="embedding-cancelled")
            existing = self.project.embedding_background
            if existing is not None and existing.is_running():
                return existing.status()
            indexer = self.project.embedding_indexer
            if indexer is not None and self._vectors_current(store, indexer):
                with binding.open_view(self.project.control) as view:
                    return dict(status="embedding-ready", model=indexer.backend.name,
                                vector_count=sum(item["vector_count"] for item in describe_sources(view, root, indexer)))
            embedding_store = self.project.embedding_store
            worker = BackgroundIndexer(
                lambda background: self._load_and_embed_project(background, root, store, embedding_store, binding),
                on_done=lambda result: self._embedding_done(binding),
                operation="embedding", root=root, index_root=store.db_path.parent,
            )
            if not self.project.tasks.register("embedding", worker):
                return dict(status="embedding-cancelled")
        return worker.status()

    def _embedding_done(self, binding):
        if not self.project.bindings.is_current(binding):
            return
        indexer = self.project.embedding_indexer
        if indexer is not None and not self._vectors_current(binding.store, indexer):
            # The last request can arrive after a pass's final revision check.
            result = self.project.embedding_background.status().get("last_result") or dict()
            if result.get("status") == "embedded":
                self.ensure_embedding_background()

    def _vectors_current(self, store, indexer):
        binding = self.project.bindings.require()
        if binding.store is not store:
            return False
        with binding.open_view(self.project.control) as view:
            return workspace_complete(view, binding.root, indexer)

    def _refresh_embedder(self) -> None:
        self.project.tasks.indexer = self._create_embedding_indexer()

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

    def _load_and_embed_project(self, background, root, store, embedding_store=None, binding=None):
        binding = binding or self.project.bindings.require()
        background.set_phase(PHASE_EMBEDDING)
        indexer = self._create_embedding_indexer(embedding_store)
        if indexer is None:
            return dict(status="embedding-unavailable", model=None,
                        error="embedding model unavailable; install semantic dependencies and configure the model")
        with self.project.tasks.lock:
            if self.project.bindings.is_current(binding):
                self.project.tasks.indexer = indexer
        indexer.check_cancelled = background.check_cancelled
        results = []
        with binding.open_view(self.project.control) as view:
            return self._embed_sources(view, background, root, indexer, results)

    def _embed_sources(self, view, background, root, indexer, results):
        for prefix, source_root, source_store, vectors in vector_sources(view, root, indexer.conn_provider):
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
        count_limit, byte_limit = retention_limits()
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
                baseline = checkpoint_revision(indexer.conn_provider, indexer.model_key)
                changes = store.changes_since(baseline)
                revision = changes["revision"]
                begin_pass(indexer.conn_provider, indexer.model_key, baseline)
                try:
                    if changes["mode"] == "full":
                        result = indexer.embed_project(root, store.iter_symbols())
                    else:
                        result = self._embed_delta(root, store, indexer, changes["paths"])
                except ServiceError as exc:
                    if exc.code not in ("source-changed", "source-too-large"):
                        raise
                    logger.warning("semantic phase=source-drift root=%s error=%s", root, exc)
                    return dict(status="embedding-waiting-for-source", error=exc.code, hint=exc.hint)
                result["mode"] = changes["mode"]
                result["reason"] = changes["reason"]
                # A concurrent index publication invalidates this pass. Reuse
                # its per-file commits and converge on the newest revision.
                if source_revision(store.get_metadata_map()) == revision:
                    mark_complete(indexer.conn_provider, indexer.model_key, revision)
                    with indexer.conn_provider.connect() as conn:
                        result["retention"] = retain_namespaces(conn, indexer.model_key, count_limit, byte_limit)
                    result["status"] = "embedded"
                    result["vector_count"] = indexer.count()
                    logger.info("semantic phase=published root=%s mode=%s revision=%s files=%s embedded=%s reused=%s",
                                root, changes["mode"], revision, result["files"], result["embedded"], result["reused"])
                    return result
            return dict(status="embedding-cancelled")
        finally:
            lock.release()

    @staticmethod
    def _embed_delta(root, store, indexer, paths):
        result = dict(embedded=0, reused=0, files=0, model=indexer.backend.name)
        for start in range(0, len(paths), 64):
            batch = paths[start:start + 64]
            groups = dict((path, []) for path in batch)
            for symbol in store.symbols_for_files(batch):
                groups[symbol["file_path"]].append(symbol)
            partial = indexer.embed_files(root, groups)
            for key in ("embedded", "reused", "files"):
                result[key] += partial[key]
        return result

    def after_incremental(self, root, store, previous, current, result):
        if self.project.enabled and self.project.store is store and result.get("status") == "incremental":
            self.maintain()

    def maintain(self):
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
