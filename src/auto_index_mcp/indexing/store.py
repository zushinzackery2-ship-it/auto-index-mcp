from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, ContextManager

from ..core.config import INDEX_VERSION
from ..core.models import FileRecord
from .sqlite import IndexDatabase
from .store_schema import initialize_schema
from .store_rows import file_row_to_dict, symbol_row_to_dict
from .store_writes import delete_file_rows, insert_child_indexes, insert_many


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

    def replace_all(
        self,
        root: str,
        records: list[FileRecord],
        child_indexes: list[dict[str, Any]] | None = None,
        extra_metadata: dict[str, Any] | None = None,
    ) -> None:
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
            insert_many(conn_to_close, records)
            insert_child_indexes(conn_to_close, child_indexes or [])
            self.set_metadata(conn_to_close, "version", INDEX_VERSION)
            self.set_metadata(conn_to_close, "root", root)
            self.set_metadata(conn_to_close, "updated_at", time.time())
            self.set_metadata(conn_to_close, "file_count", len(records))
            children = child_indexes or []
            self.set_metadata(conn_to_close, "child_index_count", len(children))
            self.set_metadata(
                conn_to_close,
                "total_file_count",
                len(records) + sum(int(child["file_count"]) for child in children),
            )
            for key, value in (extra_metadata or {}).items():
                self.set_metadata(conn_to_close, key, value)
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
                insert_many(conn, records)
                children = child_indexes or []
                insert_child_indexes(conn, children)
                self.set_metadata(conn, "version", INDEX_VERSION)
                self.set_metadata(conn, "root", root)
                self.set_metadata(conn, "updated_at", time.time())
                self.set_metadata(conn, "file_count", len(records))
                self.set_metadata(conn, "child_index_count", len(children))
                self.set_metadata(
                    conn,
                    "total_file_count",
                    len(records) + sum(int(child["file_count"]) for child in children),
                )
                for key, value in (extra_metadata or {}).items():
                    self.set_metadata(conn, key, value)
        else:
            if conn_to_close:
                conn_to_close.close()

    def replace_files(self, records: list[FileRecord]) -> None:
        if not records:
            return
        with self.connect() as conn:
            for record in records:
                self._delete_file(conn, record.path)
            insert_many(conn, records)
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
            insert_child_indexes(conn, children)
            self.set_metadata(conn, "child_index_count", len(children))
            row = conn.execute("SELECT value FROM metadata WHERE key='file_count'").fetchone()
            file_count = int(json.loads(row["value"])) if row else 0
            self.set_metadata(
                conn,
                "total_file_count",
                file_count + sum(int(child["file_count"]) for child in children),
            )
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
            self.set_metadata(conn, "total_file_count", 0)

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
        return file_row_to_dict(row) if row else None

    def all_files(self) -> list[dict[str, Any]]:
        with self.read_connect() as conn:
            rows = conn.execute("SELECT * FROM files ORDER BY path").fetchall()
        return [file_row_to_dict(row) for row in rows]

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

    def all_symbols(self) -> list[dict[str, Any]]:
        with self.read_connect() as conn:
            rows = conn.execute(
                "SELECT file_path, name, kind, line, end_line, signature, complexity "
                "FROM symbols ORDER BY file_path, line"
            ).fetchall()
        return [dict(row) for row in rows]

    def symbol_count(self) -> int:
        with self.read_connect() as conn:
            row = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()
        return int(row[0]) if row else 0

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
        return [symbol_row_to_dict(row) for row in rows]

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
        return [file_row_to_dict(row) for row in rows]

    def _delete_file(self, conn: sqlite3.Connection, path: str) -> None:
        delete_file_rows(conn, path)

    def _refresh_file_count(self, conn: sqlite3.Connection) -> None:
        count = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        self.set_metadata(conn, "file_count", count)

    def set_metadata(self, conn: sqlite3.Connection, key: str, value: Any) -> None:
        conn.execute(
            "INSERT INTO metadata VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )
