from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class IndexDatabase:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        # _configure runs inside the try so a PRAGMA failure on a corrupt DB still
        # hits finally:close(); otherwise the connection leaks and Windows refuses
        # to unlink the file during corruption recovery.
        try:
            self._configure(conn)
            yield conn
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except sqlite3.Error:
                pass
            raise
        finally:
            conn.close()

    @contextmanager
    def connect_readonly(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(f"file:{self.db_path.resolve().as_posix()}?mode=ro", timeout=30.0, uri=True)
        try:
            self._configure(conn, readonly=True)
            yield conn
        finally:
            conn.close()

    def _configure(self, conn: sqlite3.Connection, readonly: bool = False) -> None:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        if not readonly:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")
