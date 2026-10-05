"""Completion binds a source revision to one model namespace's data generation."""
from __future__ import annotations

import json
import time


def is_complete(embedding_store, model_key, revision) -> bool:
    if revision is None:
        return False
    with embedding_store.read_connect() as conn:
        return complete_in_snapshot(conn, model_key, revision)


def complete_in_snapshot(conn, model_key, revision, vector_count=None) -> bool:
    row = conn.execute("SELECT value FROM metadata WHERE key=?", ("complete:" + model_key,)).fetchone()
    marker = json.loads(row[0]) if row else None
    if revision is None or not isinstance(marker, dict) or marker.get("revision") != revision:
        return False
    count = _count(conn, model_key) if vector_count is None else vector_count
    return marker.get("generation") == _generation(conn, model_key) and marker.get("vector_count") == count


def mark_complete(embedding_store, model_key, revision) -> None:
    with embedding_store.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        marker = dict(revision=revision, generation=_generation(conn, model_key), vector_count=_count(conn, model_key))
        conn.execute(
            "INSERT INTO metadata(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            ("complete:" + model_key, json.dumps(marker)),
        )
        conn.execute("INSERT INTO metadata VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     ("checkpoint:" + model_key, json.dumps(marker)))
        conn.execute("DELETE FROM metadata WHERE key=?", ("pending:" + model_key,))
        conn.execute("INSERT INTO metadata VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     ("model-used:" + model_key, json.dumps(time.time())))


def checkpoint_revision(embedding_store, model_key):
    with embedding_store.read_connect() as conn:
        pending = conn.execute("SELECT value FROM metadata WHERE key=?", ("pending:" + model_key,)).fetchone()
        if pending:
            return json.loads(pending[0]).get("baseline")
        row = conn.execute("SELECT value FROM metadata WHERE key=?", ("checkpoint:" + model_key,)).fetchone()
        marker = json.loads(row[0]) if row else None
        if not isinstance(marker, dict):
            return None
        if marker.get("generation") != _generation(conn, model_key) or marker.get("vector_count") != _count(conn, model_key):
            return None
        return marker.get("revision")


def begin_pass(embedding_store, model_key, baseline):
    with embedding_store.connect() as conn:
        conn.execute("INSERT OR IGNORE INTO metadata VALUES (?,?)",
                     ("pending:" + model_key, json.dumps(dict(baseline=baseline))))


def invalidate(conn, model_key) -> None:
    conn.execute("DELETE FROM metadata WHERE key=?", ("complete:" + model_key,))
    conn.execute("INSERT INTO metadata VALUES (?, '1') ON CONFLICT(key) DO UPDATE SET value=CAST(value AS INTEGER)+1",
                 ("generation:" + model_key,))


def _generation(conn, model_key) -> int:
    row = conn.execute("SELECT value FROM metadata WHERE key=?", ("generation:" + model_key,)).fetchone()
    return int(row[0]) if row else 0


def _count(conn, model_key) -> int:
    return conn.execute("SELECT COUNT(*) FROM symbol_embeddings WHERE model_name=?", (model_key,)).fetchone()[0]
