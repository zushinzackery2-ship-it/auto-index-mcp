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
                "(file_path, symbol_name, symbol_line, model_name, text_hash, vector) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    file_path,
                    entry["symbol_name"],
                    entry["symbol_line"],
                    model_name,
                    entry["text_hash"],
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
            "SELECT e.file_path, e.symbol_name, e.symbol_line, e.vector, "
            "s.kind, s.end_line, s.signature, s.complexity "
            "FROM symbol_embeddings e "
            "JOIN symbols s "
            "ON s.file_path=e.file_path AND s.name=e.symbol_name AND s.line=e.symbol_line "
            "WHERE e.model_name=?",
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
        scored: list[tuple[float, dict[str, Any]]] = []
        for record in records:
            score = _dot(query_vector, record["vector"])
            if score >= min_score:
                scored.append((score, record))
        scored.sort(key=lambda item: item[0], reverse=True)
        hits: list[dict[str, Any]] = []
        for score, record in scored[: max(1, limit)]:
            hit = dict(record)
            hit["score"] = round(score, 4)
            hit.pop("vector", None)
            hits.append(hit)
        return hits
