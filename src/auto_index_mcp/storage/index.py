from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, ContextManager

from ..domain.config import INDEX_VERSION, PARSER_VERSION
from ..domain.models import FileRecord
from .sqlite import IndexDatabase
from .schema import SCHEMA_VERSION, initialize_schema
from .readers import read_files
from .generation import publish, update_policy
from .writes import delete_file_rows, insert_child_indexes, insert_many
from .symbols import query_ranked, query_relaxed
from .recovery import initialize_store, is_corruption_error, quarantine
from ..runtime.maintenance import MaintenanceLease

class IndexStore:
    def __init__(self, db_path: Path) -> None:
        self.database = IndexDatabase(db_path)
        self.db_path = self.database.db_path

    def connect(self) -> ContextManager[sqlite3.Connection]:
        return self.database.connect()

    def read_connect(self) -> ContextManager[sqlite3.Connection]:
        return self.database.connect_readonly()

    def initialize(self) -> None:
        initialize_store(self.db_path, self._schema_current, self._initialize_schema)

    def _schema_current(self) -> bool:
        with self.read_connect() as conn:
            return conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION

    def _initialize_schema(self) -> None:
        with self.connect() as conn:
            initialize_schema(conn, self.set_metadata)
            conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    def replace_all(
        self,
        root: str,
        records: list[FileRecord],
        child_indexes: list[dict[str, Any]] | None = None,
        extra_metadata: dict[str, Any] | None = None,
    ) -> None:
        try:
            self._write_all(root, records, child_indexes, extra_metadata)
        except sqlite3.DatabaseError as exc:
            # Only a genuinely corrupted database file justifies discarding it.
            # Lock contention, disk errors and programming bugs must surface so a
            # healthy index is never destroyed under a transient failure.
            if not is_corruption_error(exc):
                raise
            with MaintenanceLease(self.db_path.parent, "recover-write"):
                quarantine(self.db_path)
                self._initialize_schema()
                self._write_all(root, records, child_indexes, extra_metadata)

    def _write_all(
        self,
        root: str,
        records: list[FileRecord],
        child_indexes: list[dict[str, Any]] | None,
        extra_metadata: dict[str, Any] | None,
    ) -> None:
        # connect() runs WAL + 30s busy_timeout + foreign_keys and commits the
        # whole DELETE+INSERT as one transaction (rolling back on any error), so a
        # concurrent reader sees either the old index or the new one - never a
        # half-written mix - and a failed write leaves the existing index intact.
        children = child_indexes or []
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            extra = dict(extra_metadata or {})
            expected = extra.pop("_expected_policy_generation", None)
            row = conn.execute("SELECT value FROM metadata WHERE key='policy_generation'").fetchone()
            if expected is not None and expected != (json.loads(row[0]) if row else 0):
                raise RuntimeError("policy changed before publication")
            conn.execute("DELETE FROM files")
            conn.execute("DELETE FROM symbols")
            conn.execute("DELETE FROM child_indexes")
            insert_many(conn, records)
            insert_child_indexes(conn, children)
            self.set_metadata(conn, "version", INDEX_VERSION)
            self.set_metadata(conn, "root", root)
            self.set_metadata(conn, "file_count", len(records))
            self.set_metadata(conn, "child_index_count", len(children))
            self.set_metadata(
                conn,
                "total_file_count",
                len(records) + sum(int(child["file_count"]) for child in children),
            )
            active_paths = extra.pop("active_source_paths", [])
            conn.execute("DELETE FROM active_sources")
            conn.executemany("INSERT INTO active_sources VALUES (?)", [(path,) for path in active_paths])
            self.set_metadata(conn, "has_active_source_manifest", bool(active_paths))
            update_policy(conn, extra)
            for key, value in extra.items():
                self.set_metadata(conn, key, value)
            publish(conn, PARSER_VERSION)

    def apply_files(self, records: list[FileRecord], paths: list[str]) -> None:
        if not records and not paths:
            return
        with self.connect() as conn:
            for path in set(paths) | {record.path for record in records}:
                self._delete_file(conn, path)
            insert_many(conn, records)
            self._refresh_file_count(conn)
            self.set_metadata(conn, "version", INDEX_VERSION)
            publish(conn, PARSER_VERSION)

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
            publish(conn, PARSER_VERSION)

    def clear(self) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM files")
            conn.execute("DELETE FROM symbols")
            conn.execute("DELETE FROM child_indexes")
            self.set_metadata(conn, "version", INDEX_VERSION)
            self.set_metadata(conn, "updated_at", None)
            self.set_metadata(conn, "file_count", 0)
            self.set_metadata(conn, "child_index_count", 0)
            self.set_metadata(conn, "total_file_count", 0)

    def delete_file(self) -> None:
        if self.db_path.exists():
            self.db_path.unlink()
        for suffix in ("-wal", "-shm"):
            Path(str(self.db_path) + suffix).unlink(missing_ok=True)

    def get_metadata_map(self) -> dict[str, Any]:
        with self.read_connect() as conn:
            rows = conn.execute("SELECT key, value FROM metadata").fetchall()
        return {row["key"]: json.loads(row["value"]) for row in rows}

    def prepare_build(self) -> dict[str, Any]:
        if not self.db_path.exists():
            self.initialize()
        try:
            return self.get_metadata_map()
        except sqlite3.DatabaseError as exc:
            if not is_corruption_error(exc):
                raise
        with MaintenanceLease(self.db_path.parent, "recover-build"):
            try:
                return self.get_metadata_map()
            except sqlite3.DatabaseError as exc:
                if not is_corruption_error(exc):
                    raise
                quarantine(self.db_path)
                self._initialize_schema()
            return self.get_metadata_map()

    def update_metadata(self, values: dict[str, Any]) -> None:
        with self.connect() as conn:
            for key, value in values.items():
                self.set_metadata(conn, key, value)

    def update_policy(self, values: dict) -> int:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            return update_policy(conn, values)

    def get_file(self, path: str) -> dict[str, Any] | None:
        with self.read_connect() as conn:
            row = conn.execute("SELECT * FROM files WHERE path=?", (path,)).fetchone()
            return read_files(conn, [row])[0] if row else None

    def get_files(self, paths: list[str]) -> list[dict]:
        with self.read_connect() as conn:
            rows = []
            for start in range(0, len(paths), 400):
                batch = paths[start:start + 400]
                marks = ",".join("?" for _ in batch)
                rows.extend(conn.execute(f"SELECT * FROM files WHERE path IN ({marks})", batch))
            return read_files(conn, rows)

    def all_files(self) -> list[dict[str, Any]]:
        with self.read_connect() as conn:
            return read_files(conn, conn.execute("SELECT * FROM files ORDER BY path"))

    def is_active_source(self, path: str) -> bool:
        with self.read_connect() as conn:
            row = conn.execute("SELECT value FROM metadata WHERE key='has_active_source_manifest'").fetchone()
            if not row or not json.loads(row[0]):
                return True
            return conn.execute("SELECT 1 FROM active_sources WHERE path=?", (path,)).fetchone() is not None

    def file_headers(self) -> list[dict[str, Any]]:
        with self.read_connect() as conn:
            rows = conn.execute("SELECT path, name, parent, extension, language, size, mtime_ns, line_count, active_source, symbol_count FROM files ORDER BY path").fetchall()
        return [dict(row) for row in rows]

    def file_fingerprints(self):
        with self.read_connect() as conn:
            return [dict(row) for row in conn.execute("SELECT path, size, mtime_ns, sha1 FROM files")]

    def search_targets(self) -> list[dict[str, Any]]:
        with self.read_connect() as conn:
            rows = conn.execute("SELECT path, language, active_source FROM files ORDER BY path").fetchall()
        return [dict(row) for row in rows]

    def child_indexes(self) -> list[dict[str, Any]]:
        with self.read_connect() as conn:
            rows = conn.execute("SELECT * FROM child_indexes ORDER BY path").fetchall()
        return [dict(row) for row in rows]

    def iter_symbols(self):
        cursor = ("", -1, -1)
        while True:
            with self.read_connect() as conn:
                rows = conn.execute(
                    "SELECT id, file_path, name, kind, line, end_line, signature, complexity FROM symbols "
                    "WHERE (file_path, line, id) > (?, ?, ?) ORDER BY file_path, line, id LIMIT 256", cursor,
                ).fetchall()
            if not rows:
                return
            for row in rows:
                item = dict(row)
                cursor = (item["file_path"], item["line"], item.pop("id"))
                yield item

    def symbols_for_files(self, paths: list[str]) -> list[dict[str, Any]]:
        """Symbol rows for the given file paths only (incremental embedding)."""
        if not paths:
            return []
        rows: list[Any] = []
        with self.read_connect() as conn:
            # Chunk to stay well below SQLite's bound-parameter limit.
            for start in range(0, len(paths), 500):
                chunk = paths[start:start + 500]
                placeholders = ",".join("?" for _ in chunk)
                rows.extend(
                    conn.execute(
                        "SELECT file_path, name, kind, line, end_line, signature, complexity "
                        f"FROM symbols WHERE file_path IN ({placeholders}) "
                        "ORDER BY file_path, line",
                        chunk,
                    ).fetchall()
                )
        return [dict(row) for row in rows]

    def symbol_count(self) -> int:
        with self.read_connect() as conn:
            row = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()
        return int(row[0]) if row else 0

    def query_symbols(self, text: str, kind: str, limit: int, offset: int) -> list[dict[str, Any]]:
        with self.read_connect() as conn:
            return query_ranked(conn, text, kind, limit, offset)

    def query_symbols_relaxed(self, subtokens: list[str], kind: str, limit: int, offset: int) -> list[dict[str, Any]]:
        with self.read_connect() as conn:
            return query_relaxed(conn, subtokens, kind, limit, offset)

    def _delete_file(self, conn: sqlite3.Connection, path: str) -> None:
        delete_file_rows(conn, path)

    def _refresh_file_count(self, conn: sqlite3.Connection) -> None:
        count = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        self.set_metadata(conn, "file_count", count)
        children = conn.execute("SELECT COALESCE(SUM(file_count), 0) FROM child_indexes").fetchone()[0]
        self.set_metadata(conn, "total_file_count", count + children)

    def set_metadata(self, conn: sqlite3.Connection, key: str, value: Any) -> None:
        conn.execute(
            "INSERT INTO metadata VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )
