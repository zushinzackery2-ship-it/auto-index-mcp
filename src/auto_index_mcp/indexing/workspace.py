from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..core.config import INDEX_VERSION


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
    for db_path in root.rglob(f"{INDEX_DIR_NAME}/{INDEX_DB_NAME}"):
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


def child_indexes_to_dicts(children: list[ChildIndex]) -> list[dict[str, Any]]:
    return [child.to_dict() for child in children]


def _read_child_index(root: Path, child_root: Path, db_path: Path) -> ChildIndex | None:
    metadata = _read_metadata(db_path)
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
        file_count=int(metadata.get("file_count") or 0),
        updated_at=metadata.get("updated_at"),
        version=int(version),
    )


def _read_metadata(db_path: Path) -> dict[str, Any]:
    try:
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute("SELECT key, value FROM metadata").fetchall()
    except (OSError, sqlite3.DatabaseError):
        return {}
    try:
        return {key: json.loads(value) for key, value in rows}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
