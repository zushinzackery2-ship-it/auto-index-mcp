from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterator

from ..core.models import FileRecord
from .store_schema import initialize_schema


class IndexStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA foreign_keys=ON")
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
            initialize_schema(conn, self.set_metadata)

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

    def replace_files(self, records: list[FileRecord]) -> None:
        if not records:
            return
        with self.connect() as conn:
            for record in records:
                self._delete_file(conn, record.path)
            self._insert_many(conn, records)
            self._refresh_file_count(conn)
            self.set_metadata(conn, "updated_at", time.time())

    def delete_files(self, paths: list[str]) -> None:
        if not paths:
            return
        with self.connect() as conn:
            for path in paths:
                self._delete_file(conn, path)
            self._refresh_file_count(conn)
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

    def _delete_file(self, conn: sqlite3.Connection, path: str) -> None:
        conn.execute("DELETE FROM files WHERE path=?", (path,))
        conn.execute("DELETE FROM symbols WHERE file_path=?", (path,))
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
        return data

    def _symbol_row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["calls"] = json.loads(data["calls"] or "[]")
        data["called_by"] = json.loads(data["called_by"] or "[]")
        return data
