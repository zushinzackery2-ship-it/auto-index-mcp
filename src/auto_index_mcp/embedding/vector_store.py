from __future__ import annotations

import array
import sqlite3
from typing import Any, Iterable


def encode_vector(values: list[float]) -> bytes:
    """Encode a float vector as a compact little-endian float32 blob."""
    return array.array("f", values).tobytes()


def decode_vector(blob: bytes) -> list[float]:
    """Decode a float32 blob produced by :func:`encode_vector`."""
    packed = array.array("f")
    packed.frombytes(blob)
    return list(packed)


def _dot(a: list[float], b: list[float]) -> float:
    total = 0.0
    for idx in range(len(a)):
        total += a[idx] * b[idx]
    return total


def _score_rows(
    query_vector: list[float],
    rows: list[Any],
    min_score: float,
) -> list[tuple[float, Any]]:
    """Score every stored row by dot product with the query.

    Stored and query vectors are L2-normalized, so dot product is cosine
    similarity. The fast path builds one float32 matrix straight from the raw
    blobs (``np.frombuffer`` is zero-copy) and does a single float64 multiply;
    the pure-Python fallback decodes per row and produces the same ranking.
    """
    if not rows:
        return []
    dim = len(query_vector)
    try:
        import numpy as np

        matrix = np.frombuffer(b"".join(row["vector"] for row in rows), dtype=np.float32)
        if dim > 0 and matrix.size == len(rows) * dim:
            scores = matrix.reshape(len(rows), dim).astype(np.float64) @ np.asarray(
                query_vector, dtype=np.float64
            )
            return [
                (float(scores[index]), rows[index])
                for index in range(len(rows))
                if float(scores[index]) >= min_score
            ]
    except (ImportError, ValueError, TypeError):
        pass
    scored: list[tuple[float, Any]] = []
    for row in rows:
        vector = decode_vector(row["vector"])
        if len(vector) != dim:
            continue
        score = _dot(query_vector, vector)
        if score >= min_score:
            scored.append((score, row))
    return scored


class SymbolEmbeddingStore:
    """Persistence layer for per-symbol embedding vectors.

    Vectors are keyed by the natural symbol identity
    ``(file_path, symbol_name, symbol_line, model_name, chunk_index)`` so they
    survive the auto-increment ``symbols.id`` churn across rebuilds. Long
    symbols carry one row per overlapping text window; search aggregates them
    back to one hit per symbol. ``text_hash`` enables incremental reuse: when
    a window's embedding text is unchanged, the stored vector is reused
    instead of recomputing it.
    """

    def __init__(self) -> None:
        pass

    def entries_for(
        self, conn: sqlite3.Connection, file_path: str, model_name: str
    ) -> dict[tuple[str, int, int], tuple[str, bytes]]:
        """Existing ``(text_hash, raw vector blob)`` per window of one file.

        The blob stays encoded so reuse checks do not pay a decode for windows
        whose hash no longer matches.
        """
        rows = conn.execute(
            "SELECT symbol_name, symbol_line, chunk_index, text_hash, vector "
            "FROM symbol_embeddings WHERE file_path=? AND model_name=?",
            (file_path, model_name),
        ).fetchall()
        return {
            (row["symbol_name"], row["symbol_line"], row["chunk_index"]): (
                row["text_hash"],
                row["vector"],
            )
            for row in rows
        }

    def replace_file(
        self,
        conn: sqlite3.Connection,
        file_path: str,
        model_name: str,
        entries: Iterable[dict[str, Any]],
    ) -> None:
        conn.execute(
            "DELETE FROM symbol_embeddings WHERE file_path=? AND model_name=?",
            (file_path, model_name),
        )
        for entry in entries:
            conn.execute(
                "INSERT INTO symbol_embeddings"
                "(file_path, symbol_name, symbol_line, model_name, chunk_index, "
                "text_hash, kind, end_line, signature, complexity, vector) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    file_path,
                    entry["symbol_name"],
                    entry["symbol_line"],
                    model_name,
                    entry.get("chunk_index", 0),
                    entry["text_hash"],
                    entry.get("kind", ""),
                    entry.get("end_line", 0),
                    entry.get("signature", ""),
                    entry.get("complexity", 1),
                    encode_vector(entry["vector"]),
                ),
            )

    def delete_file(self, conn: sqlite3.Connection, file_path: str) -> None:
        conn.execute("DELETE FROM symbol_embeddings WHERE file_path=?", (file_path,))

    def purge_other_models(self, conn: sqlite3.Connection, model_name: str) -> None:
        """Drop vectors written under any other model key.

        One backend serves a store at a time, so rows keyed by an older model
        or an older config fingerprint can never be searched again.
        """
        conn.execute(
            "DELETE FROM symbol_embeddings WHERE model_name != ?", (model_name,)
        )

    def clear(self, conn: sqlite3.Connection) -> None:
        conn.execute("DELETE FROM symbol_embeddings")

    def count(self, conn: sqlite3.Connection, model_name: str) -> int:
        row = conn.execute(
            "SELECT COUNT(*) FROM symbol_embeddings WHERE model_name=?", (model_name,)
        ).fetchone()
        return int(row[0]) if row else 0

    def count_symbols(self, conn: sqlite3.Connection, model_name: str) -> int:
        """Distinct embedded symbols, regardless of how many windows each has."""
        row = conn.execute(
            "SELECT COUNT(*) FROM ("
            "SELECT DISTINCT file_path, symbol_name, symbol_line "
            "FROM symbol_embeddings WHERE model_name=?)",
            (model_name,),
        ).fetchone()
        return int(row[0]) if row else 0

    def search(
        self,
        conn: sqlite3.Connection,
        query_vector: list[float],
        model_name: str,
        limit: int,
        min_score: float = 0.0,
    ) -> list[dict[str, Any]]:
        rows = conn.execute(
            "SELECT file_path, symbol_name, symbol_line, kind, end_line, "
            "signature, complexity, vector "
            "FROM symbol_embeddings WHERE model_name=?",
            (model_name,),
        ).fetchall()
        scored = _score_rows(query_vector, rows, min_score)
        # A long symbol contributes several window vectors; its relevance is
        # the best window's score, and it must surface once, not per window.
        best: dict[tuple[str, str, int], tuple[float, Any]] = {}
        for score, row in scored:
            key = (row["file_path"], row["symbol_name"], row["symbol_line"])
            current = best.get(key)
            if current is None or score > current[0]:
                best[key] = (score, row)
        ranked = sorted(best.values(), key=lambda item: item[0], reverse=True)
        hits: list[dict[str, Any]] = []
        for score, row in ranked[: max(1, limit)]:
            hits.append(
                {
                    "file_path": row["file_path"],
                    "symbol_name": row["symbol_name"],
                    "symbol_line": row["symbol_line"],
                    "kind": row["kind"],
                    "end_line": row["end_line"],
                    "signature": row["signature"],
                    "complexity": row["complexity"],
                    "score": round(score, 4),
                }
            )
        return hits
