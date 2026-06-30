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


def _score_records(
    query_vector: list[float],
    records: list[dict[str, Any]],
    min_score: float,
) -> list[tuple[float, dict[str, Any]]]:
    """Score every record by dot product with the query.

    Stored and query vectors are L2-normalized, so dot product is cosine
    similarity. Uses a single float64 matrix multiply when numpy is available,
    and falls back to an equivalent pure-Python loop otherwise; both paths
    produce the same ranking.
    """
    if not records:
        return []
    try:
        import numpy as np

        matrix = np.asarray([record["vector"] for record in records], dtype=np.float64)
        query = np.asarray(query_vector, dtype=np.float64)
        if matrix.ndim == 2 and query.ndim == 1 and matrix.shape[1] == query.shape[0]:
            scores = matrix @ query
            return [
                (float(scores[index]), records[index])
                for index in range(len(records))
                if float(scores[index]) >= min_score
            ]
    except (ImportError, ValueError, TypeError):
        pass
    scored: list[tuple[float, dict[str, Any]]] = []
    for record in records:
        score = _dot(query_vector, record["vector"])
        if score >= min_score:
            scored.append((score, record))
    return scored


class SymbolEmbeddingStore:
    """Persistence layer for per-symbol embedding vectors.

    Vectors are keyed by the natural symbol identity
    ``(file_path, symbol_name, symbol_line, model_name)`` so they survive the
    auto-increment ``symbols.id`` churn across rebuilds. ``text_hash`` enables
    incremental reuse: when a symbol's embedding text is unchanged, the stored
    vector is reused instead of recomputing it.
    """

    def __init__(self) -> None:
        pass

    def hashes_for(
        self, conn: sqlite3.Connection, file_path: str, model_name: str
    ) -> dict[tuple[str, int], str]:
        rows = conn.execute(
            "SELECT symbol_name, symbol_line, text_hash FROM symbol_embeddings "
            "WHERE file_path=? AND model_name=?",
            (file_path, model_name),
        ).fetchall()
        return {(row["symbol_name"], row["symbol_line"]): row["text_hash"] for row in rows}

    def vectors_for(
        self, conn: sqlite3.Connection, file_path: str, model_name: str
    ) -> dict[tuple[str, int], list[float]]:
        rows = conn.execute(
            "SELECT symbol_name, symbol_line, vector FROM symbol_embeddings "
            "WHERE file_path=? AND model_name=?",
            (file_path, model_name),
        ).fetchall()
        return {
            (row["symbol_name"], row["symbol_line"]): decode_vector(row["vector"]) for row in rows
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
                "(file_path, symbol_name, symbol_line, model_name, text_hash, "
                "kind, end_line, signature, complexity, vector) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    file_path,
                    entry["symbol_name"],
                    entry["symbol_line"],
                    model_name,
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

    def clear(self, conn: sqlite3.Connection) -> None:
        conn.execute("DELETE FROM symbol_embeddings")

    def count(self, conn: sqlite3.Connection, model_name: str) -> int:
        row = conn.execute(
            "SELECT COUNT(*) FROM symbol_embeddings WHERE model_name=?", (model_name,)
        ).fetchone()
        return int(row[0]) if row else 0

    def load_all(self, conn: sqlite3.Connection, model_name: str) -> list[dict[str, Any]]:
        rows = conn.execute(
            "SELECT file_path, symbol_name, symbol_line, kind, end_line, "
            "signature, complexity, vector "
            "FROM symbol_embeddings WHERE model_name=?",
            (model_name,),
        ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            result.append(
                {
                    "file_path": row["file_path"],
                    "symbol_name": row["symbol_name"],
                    "symbol_line": row["symbol_line"],
                    "kind": row["kind"],
                    "end_line": row["end_line"],
                    "signature": row["signature"],
                    "complexity": row["complexity"],
                    "vector": decode_vector(row["vector"]),
                }
            )
        return result

    def search(
        self,
        conn: sqlite3.Connection,
        query_vector: list[float],
        model_name: str,
        limit: int,
        min_score: float = 0.0,
    ) -> list[dict[str, Any]]:
        records = self.load_all(conn, model_name)
        scored = _score_records(query_vector, records, min_score)
        scored.sort(key=lambda item: item[0], reverse=True)
        hits: list[dict[str, Any]] = []
        for score, record in scored[: max(1, limit)]:
            hit = dict(record)
            hit["score"] = round(score, 4)
            hit.pop("vector", None)
            hits.append(hit)
        return hits
