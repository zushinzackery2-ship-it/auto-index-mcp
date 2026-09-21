from __future__ import annotations

import hashlib
import logging
from itertools import groupby, islice
from pathlib import Path
from typing import Any, Iterable

from .backend import EmbeddingBackend
from .rerank import candidate_pool_size, rerank_hits
from .text import TEXT_SCHEME_VERSION, read_lines, symbol_meta, symbol_text, window_texts
from .vector_store import SymbolEmbeddingStore, encode_vector

EMBED_BATCH_SIZE = 8
logger = logging.getLogger(__name__)


class SymbolEmbedder:
    """Bounded file batches, resumable vector writes, and hybrid search."""

    def __init__(self, backend: EmbeddingBackend, conn_provider: Any) -> None:
        self.backend = backend
        self.conn_provider = conn_provider
        self.store = SymbolEmbeddingStore()
        self.progress: Any = None
        self.check_cancelled = lambda: None
        fingerprint = getattr(self.backend, "text_fingerprint", "")
        parts = [part for part in (fingerprint, TEXT_SCHEME_VERSION) if part]
        self.model_key = f"{backend.name}#{';'.join(parts)}"

    def embed_project(self, root: Path, symbols: Iterable[dict[str, Any]]) -> dict[str, Any]:
        # Callers with materialized lists may supply arbitrary order; the DB
        # streaming path already orders by file and never materializes a project.
        if isinstance(symbols, list):
            symbols = sorted(symbols, key=lambda item: item["file_path"])
        groups = ((path, list(rows)) for path, rows in groupby(symbols, key=lambda item: item["file_path"]))
        current_files: set[str] = set()
        result = self._embed_groups(root, groups, current_files)
        with self.conn_provider.connect() as conn:
            self.store.purge_other_models(conn, self.model_key)
            rows = conn.execute(
                "SELECT DISTINCT file_path FROM symbol_embeddings WHERE model_name=?", (self.model_key,)
            )
            stale = [row[0] for row in rows if row[0] not in current_files]
            for path in stale:
                self.store.delete_file(conn, path)
        return result

    def embed_files(self, root: Path, symbols_by_file: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
        return self._embed_groups(root, iter(symbols_by_file.items()), set())

    def _embed_groups(self, root, groups, current_files):
        result = dict(embedded=0, reused=0, files=0, model=self.backend.name)
        # Keep one read handle open across batches so SQLite does not checkpoint
        # and reopen its WAL after every tiny write. No read transaction is held.
        with self.conn_provider.read_connect() as reader:
            while batch := list(islice(groups, 8)):
                self.check_cancelled()
                partial = self._embed_batch(root, batch, reader)
                current_files.update(path for path, _ in batch)
                for key in ("embedded", "reused", "files"):
                    result[key] += partial[key]
                self._report_progress(result["files"], 0, result["reused"])
        self._report_progress(result["files"], result["files"], result["reused"])
        return result

    def _embed_batch(self, root, batch, reader):
        pending = []
        entries_by_file = dict()
        reused = 0
        for file_path, symbols in batch:
            old = self.store.entries_for(reader, file_path, self.model_key)
            lines = read_lines(root, file_path) if symbols else []
            entries, work = _collect_file_entries(self.backend, symbols, lines, old)
            reused += len(entries)
            entries_by_file[file_path] = entries
            pending.extend((file_path, *item) for item in work)
        for start in range(0, len(pending), EMBED_BATCH_SIZE):
            self.check_cancelled()
            chunk = pending[start:start + EMBED_BATCH_SIZE]
            vectors = self.backend.embed([item[3] for item in chunk])
            if len(vectors) != len(chunk) or any(len(v) != self.backend.dim for v in vectors):
                raise ValueError("embedding backend returned an invalid batch")
            for (path, symbol, chunk_index, _, text_hash), vector in zip(chunk, vectors):
                entry = dict(symbol_name=symbol["name"], symbol_line=symbol["line"],
                             chunk_index=chunk_index, text_hash=text_hash, vector=encode_vector(vector))
                entry.update(symbol_meta(symbol))
                entries_by_file[path].append(entry)
        # Atomic publication per bounded batch. No write lock spans inference.
        with self.conn_provider.connect() as writer:
            for path, entries in entries_by_file.items():
                self.store.replace_file(writer, path, self.model_key, entries)
        return dict(embedded=len(pending), reused=reused, files=len(batch))

    def search(self, query: str, limit: int, min_score: float = 0.0) -> list[dict[str, Any]]:
        query_vector = self.backend.embed([query])[0]
        with self.conn_provider.read_connect() as conn:
            hits = self.store.search(conn, query_vector, self.model_key, candidate_pool_size(max(1, limit)), min_score)
        return rerank_hits(query, hits, max(1, limit))

    def count(self) -> int:
        with self.conn_provider.read_connect() as conn:
            return self.store.count(conn, self.model_key)

    def count_symbols(self) -> int:
        with self.conn_provider.read_connect() as conn:
            return self.store.count_symbols(conn, self.model_key)

    def delete_files(self, paths: Iterable[str]) -> None:
        with self.conn_provider.connect() as conn:
            for path in paths:
                self.store.delete_file(conn, path)

    def _report_progress(self, done: int, total: int, reused: int) -> None:
        if self.progress is not None:
            try:
                self.progress(done, total, reused)
            except Exception:
                logger.exception("embedding progress callback failed")


def _collect_file_entries(backend, symbols, lines, existing_entries):
    entries, pending = [], []
    for symbol in symbols:
        text = symbol_text(symbol, lines)
        for chunk_index, window in enumerate(window_texts(backend, text)):
            text_hash = hashlib.sha256(window.encode("utf-8")).hexdigest()
            existing = existing_entries.get((symbol["name"], symbol["line"], chunk_index))
            if existing is None or existing[0] != text_hash:
                pending.append((symbol, chunk_index, window, text_hash))
                continue
            entry = dict(symbol_name=symbol["name"], symbol_line=symbol["line"],
                         chunk_index=chunk_index, text_hash=text_hash, vector=existing[1])
            entry.update(symbol_meta(symbol))
            entries.append(entry)
    return entries, pending
