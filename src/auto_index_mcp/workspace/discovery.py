from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..core.config import INDEX_VERSION
from ..indexing.locator import INDEX_DB_NAME, INDEX_DIR_NAME, iter_index_databases
from ..indexing.metadata_reader import DEFAULT_METADATA_READER, IndexMetadataReader


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


def discover_child_indexes(root: Path, own_db_path: Path, reader: IndexMetadataReader = DEFAULT_METADATA_READER) -> list[ChildIndex]:
    root = root.resolve()
    own_db_path = own_db_path.resolve()
    candidates: list[tuple[Path, Path]] = []
    seen_child_roots: set[Path] = set()
    for db_path in iter_index_databases(root):
        db_path = db_path.resolve()
        if db_path == own_db_path:
            continue
        child_root = db_path.parent.parent.resolve()
        if child_root == root:
            continue
        if child_root in seen_child_roots:
            continue
        seen_child_roots.add(child_root)
        candidates.append((child_root, db_path))

    children: list[ChildIndex] = []
    accepted_roots: list[Path] = []
    for child_root, db_path in sorted(candidates, key=lambda item: (len(item[0].parts), item[0].as_posix().lower())):
        if _is_nested_under_any(child_root, accepted_roots):
            continue
        child = _read_child_index(root, child_root, db_path, reader)
        if child is None:
            continue
        accepted_roots.append(child_root)
        children.append(child)
    return children


def child_indexes_to_dicts(children: list[ChildIndex]) -> list[dict[str, Any]]:
    return [child.to_dict() for child in children]


def read_index_metadata(db_path: Path) -> dict[str, Any]:
    return DEFAULT_METADATA_READER.read_metadata(db_path)


def read_child_rows(db_path: Path) -> list[dict[str, Any]]:
    return DEFAULT_METADATA_READER.read_child_rows(db_path)


def _read_child_index(root: Path, child_root: Path, db_path: Path, reader: IndexMetadataReader) -> ChildIndex | None:
    metadata = reader.read_metadata(db_path)
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
    file_count = _read_total_file_count(db_path, set(), reader)
    return ChildIndex(
        path=rel,
        root=str(child_root),
        db_path=str(db_path),
        file_count=file_count,
        updated_at=metadata.get("updated_at"),
        version=int(version),
    )


def _read_total_file_count(db_path: Path, visited: set[Path], reader: IndexMetadataReader = DEFAULT_METADATA_READER) -> int:
    db_path = db_path.resolve()
    if db_path in visited:
        return 0
    visited.add(db_path)
    metadata = reader.read_metadata(db_path)
    if not metadata:
        return 0
    total = int(metadata.get("file_count") or 0)
    for child in reader.read_child_rows(db_path):
        total += _read_total_file_count(Path(child["db_path"]), visited, reader)
    return total


def _is_nested_under_any(path: Path, roots: list[Path]) -> bool:
    return any(path != root and _is_relative_to(path, root) for root in roots)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
