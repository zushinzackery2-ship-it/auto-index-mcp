from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, ContextManager

from ..indexing.sqlite import IndexDatabase

# Bumped independently of the index schema; the vector DB owns its own lifecycle.
EMBEDDING_DB_VERSION = 1


class EmbeddingStore:
    """Standalone SQLite store for per-symbol embedding vectors.

    Lives in its own ``embeddings.db`` next to the navigable ``index.db`` so the
    vector pipeline no longer shares a write lock or schema with the code index.
    Symbol metadata needed at query time (``kind/end_line/signature/complexity``)
    is denormalized into the vector table, so search reads this database alone -
    no cross-database JOIN against the index ``symbols`` table.

    Reuses :class:`IndexDatabase` for identical WAL + busy_timeout connection
    semantics; the actual row SQL lives in :class:`SymbolEmbeddingStore`.
    """

    def __init__(self, db_path: Path) -> None:
        self.database = IndexDatabase(db_path)
        self.db_path = self.database.db_path

    def connect(self) -> ContextManager[sqlite3.Connection]:
        return self.database.connect()

    def read_connect(self) -> ContextManager[sqlite3.Connection]:
        return self.database.connect_readonly()

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS symbol_embeddings (
                    file_path TEXT NOT NULL,
                    symbol_name TEXT NOT NULL,
                    symbol_line INTEGER NOT NULL,
                    model_name TEXT NOT NULL,
                    text_hash TEXT NOT NULL,
                    kind TEXT NOT NULL DEFAULT '',
                    end_line INTEGER NOT NULL DEFAULT 0,
                    signature TEXT NOT NULL DEFAULT '',
                    complexity INTEGER NOT NULL DEFAULT 1,
                    vector BLOB NOT NULL,
                    PRIMARY KEY (file_path, symbol_name, symbol_line, model_name)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_symbol_embeddings_model ON symbol_embeddings(model_name)"
            )
            conn.execute(
                "INSERT INTO metadata VALUES ('version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (json.dumps(EMBEDDING_DB_VERSION),),
            )

    def clear(self) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM symbol_embeddings")

    def delete_file(self) -> None:
        if self.db_path.exists():
            self.db_path.unlink()
        for suffix in ("-wal", "-shm"):
            Path(str(self.db_path) + suffix).unlink(missing_ok=True)
