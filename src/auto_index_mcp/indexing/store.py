from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterator

from ..core.config import INDEX_VERSION
from ..core.models import FileRecord


class IndexStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        with self.connect() as conn:
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
            self._ensure_symbol_columns(conn)
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
            self.set_metadata(conn, "version", INDEX_VERSION)

    def replace_all(self, root: str, records: list[FileRecord], child_indexes: list[dict[str, Any]] | None = None) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM files")
            conn.execute("DELETE FROM symbols")
            conn.execute("DELETE FROM file_fts")
            conn.execute("DELETE FROM child_indexes")
            self._insert_many(conn, records)
            self._insert_child_indexes(conn, child_indexes or [])
            self.set_metadata(conn, "root", root)
            self.set_metadata(conn, "updated_at", time.time())
            self.set_metadata(conn, "file_count", len(records))
            self.set_metadata(conn, "child_index_count", len(child_indexes or []))

    def clear(self) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM files")
            conn.execute("DELETE FROM symbols")
            conn.execute("DELETE FROM file_fts")
            conn.execute("DELETE FROM child_indexes")
            self.set_metadata(conn, "updated_at", None)
            self.set_metadata(conn, "file_count", 0)
            self.set_metadata(conn, "child_index_count", 0)

    def delete_file(self) -> None:
        if self.db_path.exists():
            self.db_path.unlink()

    def get_metadata_map(self) -> dict[str, Any]:
        with self.connect() as conn:
            rows = conn.execute("SELECT key, value FROM metadata").fetchall()
        return {row["key"]: json.loads(row["value"]) for row in rows}

    def get_file(self, path: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM files WHERE path=?", (path,)).fetchone()
        return self._row_to_dict(row) if row else None

    def all_files(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM files ORDER BY path").fetchall()
        return [self._row_to_dict(row) for row in rows]

    def child_indexes(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM child_indexes ORDER BY path").fetchall()
        return [dict(row) for row in rows]

    def query_symbols(self, text: str, kind: str, limit: int, offset: int) -> list[dict[str, Any]]:
        where: list[str] = []
        params: list[Any] = []
        if text:
            where.append("(symbols.name LIKE ? OR symbols.signature LIKE ?)")
            params.extend([f"%{text}%", f"%{text}%"])
        if kind:
            where.append("symbols.kind=?")
            params.append(kind)
        sql = (
            "SELECT symbols.*, files.language FROM symbols "
            "JOIN files ON files.path=symbols.file_path"
        )
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY symbols.file_path, symbols.line LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._symbol_row_to_dict(row) for row in rows]

    def query(self, text: str, languages: list[str], parent: str, limit: int, offset: int) -> list[dict[str, Any]]:
        where: list[str] = []
        params: list[Any] = []
        table = "files"
        if text.strip():
            table = "file_fts JOIN files USING(path)"
            where.append("file_fts MATCH ?")
            params.append(text.strip())
        if languages:
            where.append("files.language IN (%s)" % ",".join("?" for _ in languages))
            params.extend(languages)
        if parent:
            where.append("(files.parent=? OR files.parent LIKE ?)")
            params.extend([parent, f"{parent}/%"])
        sql = f"SELECT files.* FROM {table}"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY files.path LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def _insert_many(self, conn: sqlite3.Connection, records: list[FileRecord]) -> None:
        for record in records:
            values = (
                record.path,
                record.name,
                record.parent,
                record.extension,
                record.language,
                record.size,
                record.mtime_ns,
                record.sha1,
                record.line_count,
                json.dumps(record.imports),
                json.dumps([asdict(symbol) for symbol in record.symbols]),
                record.snippet,
            )
            conn.execute("INSERT INTO files VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", values)
            for symbol in record.symbols:
                conn.execute(
                    """
                    INSERT INTO symbols(file_path, name, kind, line, end_line, signature, complexity, calls, called_by)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.path,
                        symbol.name,
                        symbol.kind,
                        symbol.line,
                        symbol.end_line,
                        symbol.signature,
                        symbol.complexity,
                        json.dumps(symbol.calls),
                        json.dumps(symbol.called_by),
                    ),
                )
            conn.execute(
                "INSERT INTO file_fts VALUES (?, ?, ?, ?, ?, ?, ?)",
                (record.path, record.name, record.parent, record.language, " ".join(symbol.name for symbol in record.symbols), " ".join(record.imports), record.snippet),
            )

    def _insert_child_indexes(self, conn: sqlite3.Connection, children: list[dict[str, Any]]) -> None:
        for child in children:
            conn.execute(
                "INSERT INTO child_indexes VALUES (?, ?, ?, ?, ?, ?)",
                (
                    child["path"],
                    child["root"],
                    child["db_path"],
                    child["file_count"],
                    child["updated_at"],
                    child["version"],
                ),
            )

    def set_metadata(self, conn: sqlite3.Connection, key: str, value: Any) -> None:
        conn.execute(
            "INSERT INTO metadata VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )

    def _ensure_symbol_columns(self, conn: sqlite3.Connection) -> None:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(symbols)").fetchall()}
        additions = {
            "complexity": "INTEGER NOT NULL DEFAULT 1",
            "calls": "TEXT NOT NULL DEFAULT '[]'",
            "called_by": "TEXT NOT NULL DEFAULT '[]'",
        }
        for name, definition in additions.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE symbols ADD COLUMN {name} {definition}")

    def _row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["imports"] = json.loads(data["imports"])
        data["symbols"] = json.loads(data["symbols"])
        return data

    def _symbol_row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["calls"] = json.loads(data["calls"] or "[]")
        data["called_by"] = json.loads(data["called_by"] or "[]")
        return data
