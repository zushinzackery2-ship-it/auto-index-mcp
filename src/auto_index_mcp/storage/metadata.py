from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .sqlite import IndexDatabase


class IndexMetadataReader:
    def read_metadata(self, db_path: Path) -> dict[str, Any]:
        try:
            with IndexDatabase(db_path).connect_readonly() as conn:
                rows = conn.execute("SELECT key, value FROM metadata").fetchall()
        except (OSError, sqlite3.DatabaseError):
            return {}
        try:
            return {key: json.loads(value) for key, value in rows}
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}

    def read_child_rows(self, db_path: Path) -> list[dict[str, Any]]:
        try:
            with IndexDatabase(db_path).connect_readonly() as conn:
                rows = conn.execute("SELECT db_path FROM child_indexes ORDER BY path").fetchall()
        except (OSError, sqlite3.DatabaseError):
            return []
        return [dict(row) for row in rows]


DEFAULT_METADATA_READER = IndexMetadataReader()
