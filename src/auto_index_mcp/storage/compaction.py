"""Vacuum is a maintenance operation, with explicit checkpoint failures."""
import sqlite3
from pathlib import Path


def compact_database(path: Path) -> dict:
    if not path.is_file():
        return dict(before=0, after=0, reclaimed=0)
    before = database_bytes(path)
    conn = sqlite3.connect(path, timeout=3)
    try:
        _checkpoint(conn)
        conn.execute("VACUUM")
        _checkpoint(conn)
    finally:
        conn.close()
    after = database_bytes(path)
    return dict(before=before, after=after, reclaimed=max(0, before - after))


def database_bytes(path: Path) -> int:
    return sum(candidate.stat().st_size for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm"))
               if candidate.exists())


def _checkpoint(conn) -> None:
    row = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    if row and row[0]:
        raise TimeoutError("an unmanaged SQLite connection prevents cache compaction")
