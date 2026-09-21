"""A published source revision, never a row count, proves completeness."""
from __future__ import annotations

import json


def is_complete(embedding_store, model_key, revision) -> bool:
    if revision is None:
        return False
    with embedding_store.read_connect() as conn:
        row = conn.execute("SELECT value FROM metadata WHERE key=?", ("complete:" + model_key,)).fetchone()
    return row is not None and json.loads(row[0]) == revision


def mark_complete(embedding_store, model_key, revision) -> None:
    with embedding_store.connect() as conn:
        conn.execute(
            "INSERT INTO metadata(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            ("complete:" + model_key, json.dumps(revision)),
        )
