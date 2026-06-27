from __future__ import annotations

from pathlib import Path

from ..core._utils import is_relative_to
from ..core.ignore_rules import IgnoreRules
from .locator import INDEX_DB_NAME, INDEX_DIR_NAME, iter_index_databases
from .metadata_reader import DEFAULT_METADATA_READER


def child_index_snapshot(
    root: Path,
    own_db_path: Path | None,
    boundary_roots: list[Path],
    ignore_rules: IgnoreRules,
) -> dict[str, tuple[int, ...]]:
    snapshot: dict[str, tuple[int, ...]] = {}
    own_db = own_db_path.resolve() if own_db_path else None
    for boundary in boundary_roots:
        direct_db = boundary / INDEX_DIR_NAME / INDEX_DB_NAME
        if not direct_db.exists():
            continue
        try:
            resolved = direct_db.resolve()
            if own_db and resolved == own_db:
                continue
            rel = resolved.relative_to(root).as_posix()
        except (OSError, ValueError):
            continue
        snapshot[rel] = database_fingerprint(direct_db)
    for db_path in iter_index_databases(root, boundary_roots, ignore_rules.runtime_patterns):
        try:
            resolved = db_path.resolve()
            if own_db and resolved == own_db:
                continue
            rel = resolved.relative_to(root).as_posix()
        except (OSError, ValueError):
            continue
        snapshot[rel] = database_fingerprint(db_path)
    return snapshot


def indexed_child_snapshot(root: Path, child_indexes: list[dict]) -> dict[str, tuple[int, ...]]:
    indexed_children = {}
    for child in child_indexes:
        db_path = Path(child["db_path"])
        try:
            rel = db_path.resolve().relative_to(root).as_posix()
        except (OSError, ValueError):
            rel = f"{child['path'].rstrip('/')}/{INDEX_DIR_NAME}/{INDEX_DB_NAME}"
        indexed_children[rel] = database_fingerprint(db_path)
    return indexed_children


def database_fingerprint(db_path: Path) -> tuple[int, ...]:
    values: list[int] = []
    for path in (db_path, Path(f"{db_path}-wal"), Path(f"{db_path}-shm")):
        try:
            stat = path.stat()
            values.extend([stat.st_size, stat.st_mtime_ns])
        except OSError:
            values.extend([0, 0])
    metadata = DEFAULT_METADATA_READER.read_metadata(db_path)
    values.extend(
        [
            int(float(metadata.get("updated_at") or 0) * 1_000_000_000),
            int(metadata.get("file_count") or 0),
            int(metadata.get("child_index_count") or 0),
            int(metadata.get("version") or 0),
        ]
    )
    return tuple(values)


def is_index_related_path(path: Path) -> bool:
    parts = path.parts
    if INDEX_DIR_NAME not in parts:
        return False
    return path.name in {INDEX_DB_NAME, f"{INDEX_DB_NAME}-wal", f"{INDEX_DB_NAME}-shm"} or INDEX_DB_NAME in parts


def direct_child_index_db(path: Path, own_db: Path | None) -> Path | None:
    direct_db = path / INDEX_DIR_NAME / INDEX_DB_NAME
    if not direct_db.exists():
        return None
    try:
        resolved = direct_db.resolve()
    except OSError:
        return None
    if own_db and resolved == own_db:
        return None
    return resolved


def is_own_database_path(path: Path, own_db: Path | None) -> bool:
    if own_db is None:
        return False
    try:
        resolved = path.resolve()
    except OSError:
        return False
    return resolved in {own_db, Path(f"{own_db}-wal"), Path(f"{own_db}-shm")}


def is_under_boundary(path: Path, boundary_roots: list[Path]) -> bool:
    return any(is_relative_to(path, boundary) for boundary in boundary_roots)


def subtree_had_child_index(child_indexes: dict[str, tuple[int, ...]], rel: str) -> bool:
    prefix = rel.rstrip("/") + "/"
    return any(path == rel or path.startswith(prefix) for path in child_indexes)
