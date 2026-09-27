from __future__ import annotations

import heapq
import sqlite3

from ..storage.completion import is_complete
from ..storage.embeddings import EmbeddingStore
from ..embedding.rerank import candidate_pool_size, rerank_hits
from ..storage.vectors import SymbolEmbeddingStore
from ..storage.generation import revision


def vector_sources(view, root, primary):
    for prefix, source_root, store in view.iter_sources(root):
        vectors = primary if not prefix else EmbeddingStore(store.db_path.parent / "embeddings.db")
        yield prefix, source_root, store, vectors


def workspace_complete(view, root, indexer) -> bool:
    return all(vectors.db_path.exists() and is_complete(vectors, indexer.model_key, revision(store.get_metadata_map()))
               for _, _, store, vectors in vector_sources(view, root, indexer.conn_provider))


def describe_sources(view, root, indexer) -> list[dict]:
    return [_describe(prefix, store, vectors, indexer.model_key)
            for prefix, _, store, vectors in vector_sources(view, root, indexer.conn_provider)]


def _describe(prefix, store, vectors, model_key) -> dict:
    metadata = store.get_metadata_map()
    result = dict(path=prefix, source_generation=metadata.get("source_generation", 0),
                  vector_count=0, embedded_symbol_count=0, complete=False, state="building")
    if not vectors.db_path.exists():
        return result
    try:
        complete = is_complete(vectors, model_key, revision(metadata))
        with vectors.read_connect() as conn:
            vector_store = SymbolEmbeddingStore()
            count = vector_store.count(conn, model_key)
            symbols = vector_store.count_symbols(conn, model_key)
        state = ("ready" if count else "empty") if complete else ("partial" if count else "building")
        result.update(vector_count=count, embedded_symbol_count=symbols, complete=complete, state=state)
    except (OSError, sqlite3.Error) as exc:
        result.update(state="failed", error=str(exc))
    return result


def search_workspace(view, root, indexer, query, limit, min_score):
    pool = candidate_pool_size(limit)
    query_vector = indexer.backend.embed([query])[0]
    heap, sources = [], []
    sequence = 0
    for prefix, _, store, vectors in vector_sources(view, root, indexer.conn_provider):
        state = _describe(prefix, store, vectors, indexer.model_key)
        sources.append(state)
        if not state["vector_count"]:
            continue
        try:
            with vectors.read_connect() as conn:
                hits = indexer.store.search(conn, query_vector, indexer.model_key, pool, min_score)
        except (OSError, sqlite3.Error) as exc:
            state.update(state="failed", complete=False, error=str(exc))
            continue
        for hit in hits:
            hit["file_path"] = "/".join(part for part in (prefix, hit["file_path"]) if part)
            hit["source_generation"] = state["source_generation"]
            entry = (hit["score"], -sequence, hit)
            sequence += 1
            if len(heap) < pool:
                heapq.heappush(heap, entry)
            elif entry[:2] > heap[0][:2]:
                heapq.heapreplace(heap, entry)
    hits = [entry[2] for entry in sorted(heap, key=lambda row: row[:2], reverse=True)]
    return rerank_hits(query, hits, limit), sources
