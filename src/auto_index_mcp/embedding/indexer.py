from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Iterable

from ..core.text_decode import read_text_file
from .backend import EmbeddingBackend
from .vector_store import SymbolEmbeddingStore

MAX_BODY_LINES = 64
MAX_BODY_CHARS = 2000


class SymbolEmbedder:
    """Builds and maintains per-symbol embedding vectors.

    The embedder reads symbols straight from the index (``symbols`` table),
    enriches each one with a slice of its source body, and stores L2-normalized
    vectors via :class:`SymbolEmbeddingStore`. ``text_hash`` lets unchanged
    symbols skip re-embedding across rebuilds and incremental watcher updates.
    """

    def __init__(self, backend: EmbeddingBackend, conn_provider: Any) -> None:
        self.backend = backend
        self.conn_provider = conn_provider
        self.store = SymbolEmbeddingStore()

    def embed_project(self, root: Path, symbols: list[dict[str, Any]]) -> dict[str, Any]:
        grouped = _group_symbols_by_file(symbols)
        result = self._embed_files(root, grouped)
        model_name = self.backend.name
        current_files = set(grouped.keys())
        with self.conn_provider.connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT file_path FROM symbol_embeddings WHERE model_name=?",
                (model_name,),
            ).fetchall()
            for row in rows:
                if row["file_path"] not in current_files:
                    self.store.delete_file(conn, row["file_path"])
        return result

    def embed_files(self, root: Path, symbols_by_file: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
        return self._embed_files(root, symbols_by_file)

    def search(
        self,
        conn_provider: Any,
        query: str,
        limit: int,
        min_score: float = 0.0,
    ) -> list[dict[str, Any]]:
        query_vector = self.backend.embed([query])[0]
        with conn_provider.read_connect() as conn:
            return self.store.search(conn, query_vector, self.backend.name, limit, min_score)

    def count(self, conn_provider: Any) -> int:
        with conn_provider.read_connect() as conn:
            return self.store.count(conn, self.backend.name)

    def _embed_files(
        self, root: Path, symbols_by_file: dict[str, list[dict[str, Any]]]
    ) -> dict[str, Any]:
        model_name = self.backend.name
        embedded = 0
        reused = 0
        files = 0
        with self.conn_provider.connect() as conn:
            for file_path, symbols in symbols_by_file.items():
                files += 1
                existing_hashes = self.store.hashes_for(conn, file_path, model_name)
                existing_vectors = self.store.vectors_for(conn, file_path, model_name)
                lines = _read_lines_safe(root, file_path)
                pending: list[tuple[dict[str, Any], str, str]] = []
                entries: list[dict[str, Any]] = []
                for symbol in symbols:
                    text = _symbol_text(symbol, lines)
                    text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
                    key = (symbol["name"], symbol["line"])
                    if existing_hashes.get(key) == text_hash and key in existing_vectors:
                        entries.append(
                            {
                                "symbol_name": symbol["name"],
                                "symbol_line": symbol["line"],
                                "text_hash": text_hash,
                                "vector": existing_vectors[key],
                            }
                        )
                        reused += 1
                    else:
                        pending.append((symbol, text, text_hash))
                if pending:
                    vectors = self.backend.embed([item[1] for item in pending])
                    for (symbol, _, text_hash), vector in zip(pending, vectors):
                        entries.append(
                            {
                                "symbol_name": symbol["name"],
                                "symbol_line": symbol["line"],
                                "text_hash": text_hash,
                                "vector": vector,
                            }
                        )
                        embedded += 1
                self.store.replace_file(conn, file_path, model_name, entries)
        return {"embedded": embedded, "reused": reused, "files": files, "model": model_name}


def _group_symbols_by_file(
    symbols: Iterable[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for symbol in symbols:
        grouped.setdefault(symbol["file_path"], []).append(symbol)
    return grouped


def _read_lines_safe(root: Path, file_path: str) -> list[str]:
    try:
        return read_text_file(root / file_path).splitlines()
    except (OSError, UnicodeDecodeError):
        return []


def _symbol_text(symbol: dict[str, Any], lines: list[str]) -> str:
    kind = symbol.get("kind", "")
    name = symbol.get("name", "")
    signature = symbol.get("signature", "") or ""
    head = f"{kind} {signature}" if signature else f"{kind} {name}".strip()
    start = max(1, int(symbol.get("line", 1))) - 1
    end = int(symbol.get("end_line", start + 1))
    body_lines = lines[start:end][:MAX_BODY_LINES]
    body = "\n".join(body_lines)
    if len(body) > MAX_BODY_CHARS:
        body = body[:MAX_BODY_CHARS]
    return f"{head}\n{body}".strip()
