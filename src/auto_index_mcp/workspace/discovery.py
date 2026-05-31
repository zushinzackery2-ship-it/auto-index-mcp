from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..core.config import DEFAULT_EXCLUDE_DIRS, INDEX_VERSION


INDEX_DB_NAME = "index.db"
INDEX_DIR_NAME = ".auto-index-mcp"


@dataclass(frozen=True)
class ChildIndex:
    path: str
    root: str
    db_path: str
    file_count: int
    updated_at: float | None
    version: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def discover_child_indexes(root: Path, own_db_path: Path) -> list[ChildIndex]:
    root = root.resolve()
    own_db_path = own_db_path.resolve()
    candidates: list[tuple[Path, Path]] = []
    for db_path in iter_index_databases(root):
        db_path = db_path.resolve()
        if db_path == own_db_path:
            continue
        child_root = db_path.parent.parent.resolve()
        if child_root == root:
            continue
        candidates.append((child_root, db_path))

    children: list[ChildIndex] = []
    accepted_roots: list[Path] = []
    for child_root, db_path in sorted(candidates, key=lambda item: len(item[0].parts)):
        if any(_is_relative_to(child_root, accepted_root) for accepted_root in accepted_roots):
            continue
        child = _read_child_index(root, child_root, db_path)
        if child is None:
            continue
        children.append(child)
        accepted_roots.append(child_root)
    return children


def iter_index_databases(root: Path) -> list[Path]:
    root = root.resolve()
    matches: list[Path] = []
    for dir_path, dir_names, file_names in os.walk(root):
        current = Path(dir_path)
        dir_names[:] = [name for name in dir_names if not _should_skip_dir(current / name)]
        if current.name == INDEX_DIR_NAME and INDEX_DB_NAME in file_names:
            matches.append(current / INDEX_DB_NAME)
            dir_names[:] = []
    return matches


def child_indexes_to_dicts(children: list[ChildIndex]) -> list[dict[str, Any]]:
    return [child.to_dict() for child in children]


def read_index_metadata(db_path: Path) -> dict[str, Any]:
    try:
        with sqlite3.connect(f"file:{db_path.resolve().as_posix()}?mode=ro", uri=True) as conn:
            rows = conn.execute("SELECT key, value FROM metadata").fetchall()
    except (OSError, sqlite3.DatabaseError):
        return {}
    try:
        return {key: json.loads(value) for key, value in rows}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def read_child_rows(db_path: Path) -> list[dict[str, Any]]:
    try:
        with sqlite3.connect(f"file:{db_path.resolve().as_posix()}?mode=ro", uri=True) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT db_path FROM child_indexes ORDER BY path").fetchall()
    except (OSError, sqlite3.DatabaseError):
        return []
    return [dict(row) for row in rows]


def _read_child_index(root: Path, child_root: Path, db_path: Path) -> ChildIndex | None:
    metadata = read_index_metadata(db_path)
    if not metadata:
        return None
    metadata_root = metadata.get("root")
    version = metadata.get("version")
    if version != INDEX_VERSION or not metadata_root:
        return None
    try:
        if Path(str(metadata_root)).resolve() != child_root:
            return None
        rel = child_root.relative_to(root).as_posix()
    except (OSError, ValueError):
        return None
    return ChildIndex(
        path=rel,
        root=str(child_root),
        db_path=str(db_path),
        file_count=_read_total_file_count(db_path, set()),
        updated_at=metadata.get("updated_at"),
        version=int(version),
    )


def _read_total_file_count(db_path: Path, visited: set[Path]) -> int:
    db_path = db_path.resolve()
    if db_path in visited:
        return 0
    visited.add(db_path)
    metadata = read_index_metadata(db_path)
    total = int(metadata.get("file_count") or 0)
    for child in read_child_rows(db_path):
        total += _read_total_file_count(Path(child["db_path"]), visited)
    return total


def _should_skip_dir(path: Path) -> bool:
    resolved = path.resolve()
    if resolved.name == INDEX_DIR_NAME:
        return False
    return any(part in DEFAULT_EXCLUDE_DIRS for part in resolved.parts)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False

