from __future__ import annotations

from contextlib import contextmanager

from ..leases import BuildLock


@contextmanager
def schema_lock(db_path):
    """Serialize first-time DDL before opening SQLite's writable handle."""
    lock = BuildLock(db_path.with_suffix(".schema.lock"))
    if not lock.acquire(3.0):
        raise TimeoutError(f"schema initialization is busy: {db_path}")
    try:
        yield
    finally:
        lock.release()
