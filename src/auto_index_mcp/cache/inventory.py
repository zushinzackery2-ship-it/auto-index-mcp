"""Recursive space accounting never follows links outside a cache."""
import os
import sqlite3
from pathlib import Path
from stat import S_ISLNK


def is_link(path: Path) -> bool:
    stat = path.lstat()
    return S_ISLNK(stat.st_mode) or bool(getattr(stat, "st_file_attributes", 0) & 0x400)


def file_inventory(directory: Path) -> dict:
    total = 0
    errors = []
    directory = Path(directory)
    if not directory.exists():
        return dict(bytes=0, partial=False, errors=[])
    if not _owned_path(directory, errors):
        return dict(bytes=0, partial=True, errors=errors or [f"linked cache root: {directory}"])
    for current, names, files in os.walk(directory, followlinks=False, onerror=lambda exc: _error(errors, exc)):
        base = Path(current)
        names[:] = [name for name in names if _owned_path(base / name, errors)]
        for name in files:
            path = base / name
            try:
                if _owned_path(path, errors, missing_ok=True):
                    total += path.stat().st_size
            except FileNotFoundError:
                continue
            except OSError as exc:
                _error(errors, exc)
    return dict(bytes=total, partial=bool(errors), errors=errors)


def _error(errors, exc) -> None:
    if len(errors) < 5:
        errors.append(str(exc))


def _owned_path(path, errors, missing_ok=False) -> bool:
    try:
        return not is_link(path)
    except FileNotFoundError as exc:
        # SQLite removes WAL/SHM after a connection closes. A vanished file
        # contributes no bytes; directory loss still makes the scan incomplete.
        if not missing_ok:
            _error(errors, exc)
        return False
    except OSError as exc:
        _error(errors, exc)
        return False


def sqlite_space(path: Path) -> dict:
    if not path.is_file():
        return dict(allocated_bytes=0, reusable_bytes=0)
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=3)
    try:
        conn.execute("BEGIN")
        page_size = conn.execute("PRAGMA page_size").fetchone()[0]
        pages = conn.execute("PRAGMA page_count").fetchone()[0]
        free = conn.execute("PRAGMA freelist_count").fetchone()[0]
        return dict(allocated_bytes=page_size * pages, reusable_bytes=page_size * free)
    finally:
        conn.close()


def cache_inventory(directory: Path) -> dict:
    directory = Path(directory)
    result = file_inventory(directory)
    result["databases"] = dict()
    result["reusable_bytes"] = 0
    if not directory.exists() or not _owned_path(directory, result["errors"]):
        result["partial"] = bool(result["errors"])
        return result
    for name in ("index.db", "embeddings.db"):
        try:
            path = directory / name
            if path.exists() and is_link(path):
                raise OSError(f"linked database excluded from cache accounting: {path}")
            result["databases"][name] = sqlite_space(path)
        except (OSError, sqlite3.Error) as exc:
            result["databases"][name] = dict(error=str(exc))
            _error(result["errors"], exc)
    result["reusable_bytes"] = sum(item.get("reusable_bytes", 0) for item in result["databases"].values())
    vectors = Path(directory) / "embeddings.db"
    if vectors.is_file() and "error" not in result["databases"]["embeddings.db"]:
        from ..storage.embeddings import EmbeddingStore
        from ..storage.namespace import namespaces
        try:
            with EmbeddingStore(vectors).read_connect() as conn:
                result["models"] = namespaces(conn)
        except sqlite3.Error as exc:
            result["models_error"] = str(exc)
            _error(result["errors"], exc)
    result["partial"] = bool(result["errors"])
    return result
