"""Completion binds a source revision to one model namespace's data generation."""
from __future__ import annotations

import json


def is_complete(embedding_store, model_key, revision) -> bool:
    if revision is None:
        return False
    with embedding_store.read_connect() as conn:
        row = conn.execute("SELECT value FROM metadata WHERE key=?", ("complete:" + model_key,)).fetchone()
        marker = json.loads(row[0]) if row else None
        if not isinstance(marker, dict) or marker.get("revision") != revision:
            return False
        return marker.get("generation") == _generation(conn, model_key) and marker.get("vector_count") == _count(conn, model_key)


def mark_complete(embedding_store, model_key, revision) -> None:
    with embedding_store.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        marker = dict(revision=revision, generation=_generation(conn, model_key), vector_count=_count(conn, model_key))
        conn.execute(
            "INSERT INTO metadata(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            ("complete:" + model_key, json.dumps(marker)),
        )


def invalidate(conn, model_key) -> None:
    conn.execute("DELETE FROM metadata WHERE key=?", ("complete:" + model_key,))
    conn.execute("INSERT INTO metadata VALUES (?, '1') ON CONFLICT(key) DO UPDATE SET value=CAST(value AS INTEGER)+1",
                 ("generation:" + model_key,))


def _generation(conn, model_key) -> int:
    row = conn.execute("SELECT value FROM metadata WHERE key=?", ("generation:" + model_key,)).fetchone()
    return int(row[0]) if row else 0


def _count(conn, model_key) -> int:
    return conn.execute("SELECT COUNT(*) FROM symbol_embeddings WHERE model_name=?", (model_key,)).fetchone()[0]
