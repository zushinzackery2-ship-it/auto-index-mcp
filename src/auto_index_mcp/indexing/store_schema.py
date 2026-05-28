from __future__ import annotations

import sqlite3
from typing import Any

from ..core.config import INDEX_VERSION


def initialize_schema(conn: sqlite3.Connection, set_metadata: Any) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS files (
            path TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            parent TEXT NOT NULL,
            extension TEXT NOT NULL,
            language TEXT NOT NULL,
            size INTEGER NOT NULL,
            mtime_ns INTEGER NOT NULL,
            sha1 TEXT NOT NULL,
            line_count INTEGER NOT NULL,
            imports TEXT NOT NULL,
            symbols TEXT NOT NULL,
            snippet TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS symbols (
            id INTEGER PRIMARY KEY,
            file_path TEXT NOT NULL,
            name TEXT NOT NULL,
            kind TEXT NOT NULL,
            line INTEGER NOT NULL,
            end_line INTEGER NOT NULL,
            signature TEXT NOT NULL,
            complexity INTEGER NOT NULL DEFAULT 1,
            calls TEXT NOT NULL DEFAULT '[]',
            called_by TEXT NOT NULL DEFAULT '[]',
            FOREIGN KEY(file_path) REFERENCES files(path) ON DELETE CASCADE
        )
        """
    )
    ensure_symbol_columns(conn)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_symbols_file ON symbols(file_path)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS child_indexes (
            path TEXT PRIMARY KEY,
            root TEXT NOT NULL,
            db_path TEXT NOT NULL,
            file_count INTEGER NOT NULL,
            updated_at REAL,
            version INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS file_fts USING fts5(path UNINDEXED, name, parent, language, symbols, imports, snippet)"
    )
    set_metadata(conn, "version", INDEX_VERSION)


def ensure_symbol_columns(conn: sqlite3.Connection) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(symbols)").fetchall()}
    additions = {
        "complexity": "INTEGER NOT NULL DEFAULT 1",
        "calls": "TEXT NOT NULL DEFAULT '[]'",
        "called_by": "TEXT NOT NULL DEFAULT '[]'",
    }
    for name, definition in additions.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE symbols ADD COLUMN {name} {definition}")
