from __future__ import annotations

import logging
import sqlite3
import uuid
from pathlib import Path

from ..runtime.locking.schema import schema_lock
from ..runtime.maintenance import MaintenanceLease

logger = logging.getLogger(__name__)
CORRUPTION_TOKENS = ("malformed", "not a database", "disk image", "file is encrypted")


def is_corruption_error(exc: sqlite3.DatabaseError) -> bool:
    code = getattr(exc, "sqlite_errorcode", 0) & 0xFF
    return code in (sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB) or any(
        token in str(exc).lower() for token in CORRUPTION_TOKENS
    )


def quarantine(db_path: Path) -> None:
    token = uuid.uuid4().hex
    for suffix in ("", "-wal", "-shm", "-journal"):
        path = Path(str(db_path) + suffix)
        if path.exists():
            path.replace(path.with_name(f"{path.name}.corrupt-{token}"))
    logger.warning("database quarantined path=%s recovery_id=%s", db_path, token)


def initialize_store(db_path, current, initialize_schema) -> None:
    try:
        if db_path.exists() and current():
            return
    except sqlite3.DatabaseError as exc:
        if not is_corruption_error(exc):
            raise
        with MaintenanceLease(db_path.parent, "recover"):
            try:
                if current():
                    return
            except sqlite3.DatabaseError as retry:
                if not is_corruption_error(retry):
                    raise
                quarantine(db_path)
            initialize_schema()
        return
    with schema_lock(db_path):
        if not db_path.exists():
            initialize_schema()
            return
        if current():
            return
    # Upgrading an existing schema must exclude old watcher/writer processes,
    # not just other schema initializers. Release the schema lease first so
    # maintenance can acquire the complete lock set in its canonical order.
    with MaintenanceLease(db_path.parent, "schema-upgrade"):
        if not current():
            initialize_schema()
