from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, ContextManager

from ..core.config import INDEX_VERSION
from ..core.models import FileRecord
from .sqlite import IndexDatabase
from .store_schema import initialize_schema


class IndexStore:
    def __init__(self, db_path: Path) -> None:
        self.database = IndexDatabase(db_path)
        self.db_path = self.database.db_path

    def connect(self) -> ContextManager[sqlite3.Connection]:
        return self.database.connect()

    def read_connect(self) -> ContextManager[sqlite3.Connection]:
        return self.database.connect_readonly()

    def initialize(self) -> None:
        with self.connect() as conn:
            initialize_schema(conn, self.set_metadata)

    def replace_all(self, root: str, records: list[FileRecord], child_indexes: list[dict[str, Any]] | None = None) -> None:
        # Handle corrupted database by deleting and recreating
        conn_to_close = None
        try:
            conn_to_close = sqlite3.connect(self.db_path, timeout=1.0)
            conn_to_close.execute("PRAGMA busy_timeout=1000")
            conn_to_close.execute("DELETE FROM files")
            conn_to_close.execute("DELETE FROM symbols")
            conn_to_close.execute("DELETE FROM symbol_nesting")
            conn_to_close.execute("DELETE FROM file_fts")
            conn_to_close.execute("DELETE FROM child_indexes")
            self._insert_many(conn_to_close, records)
            self._insert_child_indexes(conn_to_close, child_indexes or [])
            self.set_metadata(conn_to_close, "version", INDEX_VERSION)
            self.set_metadata(conn_to_close, "root", root)
            self.set_metadata(conn_to_close, "updated_at", time.time())
            self.set_metadata(conn_to_close, "file_count", len(records))
            self.set_metadata(conn_to_close, "child_index_count", len(child_indexes or []))
            conn_to_close.commit()
        except Exception:
            # Database is corrupted, delete and reinitialize
            if conn_to_close:
                conn_to_close.close()
            self.db_path.unlink(missing_ok=True)
            self.initialize()
            with self.connect() as conn:
                conn.execute("DELETE FROM files")
                conn.execute("DELETE FROM symbols")
                conn.execute("DELETE FROM symbol_nesting")
                conn.execute("DELETE FROM file_fts")
                conn.execute("DELETE FROM child_indexes")
                self._insert_many(conn, records)
                self._insert_child_indexes(conn, child_indexes or [])
                self.set_metadata(conn, "version", INDEX_VERSION)
                self.set_metadata(conn, "root", root)
                self.set_metadata(conn, "updated_at", time.time())
                self.set_metadata(conn, "file_count", len(records))
                self.set_metadata(conn, "child_index_count", len(child_indexes or []))
        else:
            if conn_to_close:
                conn_to_close.close()

    def replace_files(self, records: list[FileRecord]) -> None:
        if not records:
            return
        with self.connect() as conn:
            for record in records:
                self._delete_file(conn, record.path)
            self._insert_many(conn, records)
            self._refresh_file_count(conn)
            self.set_metadata(conn, "version", INDEX_VERSION)
            self.set_metadata(conn, "updated_at", time.time())

    def delete_files(self, paths: list[str]) -> None:
        if not paths:
            return
        with self.connect() as conn:
            for path in paths:
                self._delete_file(conn, path)
            self._refresh_file_count(conn)
            self.set_metadata(conn, "version", INDEX_VERSION)
            self.set_metadata(conn, "updated_at", time.time())

    def replace_child_indexes(self, children: list[dict[str, Any]]) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM child_indexes")
            self._insert_child_indexes(conn, children)
            self.set_metadata(conn, "child_index_count", len(children))
            self.set_metadata(conn, "updated_at", time.time())

    def clear(self) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM files")
            conn.execute("DELETE FROM symbols")
            conn.execute("DELETE FROM symbol_nesting")
            conn.execute("DELETE FROM file_fts")
            conn.execute("DELETE FROM child_indexes")
            self.set_metadata(conn, "version", INDEX_VERSION)
            self.set_metadata(conn, "updated_at", None)
            self.set_metadata(conn, "file_count", 0)
            self.set_metadata(conn, "child_index_count", 0)

    def delete_file(self) -> None:
        if self.db_path.exists():
            self.db_path.unlink()

    def get_metadata_map(self) -> dict[str, Any]:
        with self.read_connect() as conn:
            rows = conn.execute("SELECT key, value FROM metadata").fetchall()
        return {row["key"]: json.loads(row["value"]) for row in rows}

    def get_file(self, path: str) -> dict[str, Any] | None:
        with self.read_connect() as conn:
            row = conn.execute("SELECT * FROM files WHERE path=?", (path,)).fetchone()
        return self._row_to_dict(row) if row else None

    def all_files(self) -> list[dict[str, Any]]:
        with self.read_connect() as conn:
            rows = conn.execute("SELECT * FROM files ORDER BY path").fetchall()
        return [self._row_to_dict(row) for row in rows]

    def file_headers(self) -> list[dict[str, Any]]:
        with self.read_connect() as conn:
            rows = conn.execute("SELECT path, name, parent, extension, language, size, mtime_ns, line_count, active_source FROM files ORDER BY path").fetchall()
        return [dict(row) for row in rows]

    def search_targets(self) -> list[dict[str, Any]]:
        with self.read_connect() as conn:
            rows = conn.execute("SELECT path, language, active_source FROM files ORDER BY path").fetchall()
        return [dict(row) for row in rows]

    def child_indexes(self) -> list[dict[str, Any]]:
        with self.read_connect() as conn:
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
        with self.read_connect() as conn:
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
        with self.read_connect() as conn:
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
                json.dumps(record.quality_findings),
                1 if record.active_source else 0,
                record.snippet,
            )
            conn.execute(
                "INSERT INTO files(path, name, parent, extension, language, size, mtime_ns, sha1, line_count, imports, symbols, quality_findings, active_source, snippet) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                values,
            )
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
                    """
                    INSERT INTO symbol_nesting(
                        file_path, symbol_name, symbol_kind, line, end_line, parent_name, parent_kind,
                        depth, nesting_path, children_count, max_child_depth, max_block_depth
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.path,
                        symbol.name,
                        symbol.kind,
                        symbol.line,
                        symbol.end_line,
                        symbol.parent_name,
                        symbol.parent_kind,
                        symbol.depth,
                        symbol.nesting_path or symbol.name,
                        symbol.children_count,
                        symbol.max_child_depth,
                        symbol.max_block_depth,
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

    def _delete_file(self, conn: sqlite3.Connection, path: str) -> None:
        conn.execute("DELETE FROM files WHERE path=?", (path,))
        conn.execute("DELETE FROM symbols WHERE file_path=?", (path,))
        conn.execute("DELETE FROM symbol_nesting WHERE file_path=?", (path,))
        conn.execute("DELETE FROM file_fts WHERE path=?", (path,))

    def _refresh_file_count(self, conn: sqlite3.Connection) -> None:
        count = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        self.set_metadata(conn, "file_count", count)

    def set_metadata(self, conn: sqlite3.Connection, key: str, value: Any) -> None:
        conn.execute(
            "INSERT INTO metadata VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )

    def _row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["imports"] = json.loads(data["imports"])
        data["symbols"] = json.loads(data["symbols"])
        data["quality_findings"] = json.loads(data.get("quality_findings") or "[]")
        data["active_source"] = bool(data.get("active_source", 1))
        return data

    def _symbol_row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["calls"] = json.loads(data["calls"] or "[]")
        data["called_by"] = json.loads(data["called_by"] or "[]")
        return data
