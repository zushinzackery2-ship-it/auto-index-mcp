from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ..core.config import DEFAULT_EXCLUDE_DIRS, TEXT_EXTENSIONS
from ..workspace.discovery import INDEX_DB_NAME, INDEX_DIR_NAME


@dataclass(frozen=True)
class WatchSnapshot:
    files: dict[str, tuple[int, int]]
    child_indexes: dict[str, tuple[int, ...]]

    def changed_files(self, previous: "WatchSnapshot") -> tuple[list[str], list[str], list[str]]:
        added = sorted(set(self.files) - set(previous.files))
        deleted = sorted(set(previous.files) - set(self.files))
        modified = sorted(path for path in set(self.files) & set(previous.files) if self.files[path] != previous.files[path])
        return added, deleted, modified

    def child_indexes_changed(self, previous: "WatchSnapshot") -> bool:
        return self.child_indexes != previous.child_indexes

    def child_index_changes(self, previous: "WatchSnapshot") -> tuple[list[str], list[str], list[str]]:
        added = sorted(set(self.child_indexes) - set(previous.child_indexes))
        deleted = sorted(set(previous.child_indexes) - set(self.child_indexes))
        modified = sorted(
            path
            for path in set(self.child_indexes) & set(previous.child_indexes)
            if self.child_indexes[path] != previous.child_indexes[path]
        )
        return added, deleted, modified


def take_watch_snapshot(
    root: Path,
    boundary_roots: list[Path] | None = None,
    own_db_path: Path | None = None,
) -> WatchSnapshot:
    root = root.resolve()
    boundaries = [path.resolve() for path in boundary_roots or []]
    files: dict[str, tuple[int, int]] = {}
    for path in _iter_source_files(root, boundaries):
        try:
            stat = path.stat()
            rel = path.resolve().relative_to(root).as_posix()
        except (OSError, ValueError):
            continue
        files[rel] = (stat.st_size, stat.st_mtime_ns)
    return WatchSnapshot(files=files, child_indexes=_child_index_snapshot(root, own_db_path, boundaries))


def snapshot_from_index(root: Path, files: list[dict], child_indexes: list[dict]) -> WatchSnapshot:
    root = root.resolve()
    indexed_files = {
        item["path"]: (int(item.get("size", 0)), int(item.get("mtime_ns", 0)))
        for item in files
    }
    indexed_children = {}
    for child in child_indexes:
        db_path = Path(child["db_path"])
        try:
            rel = db_path.resolve().relative_to(root).as_posix()
        except (OSError, ValueError):
            rel = f"{child['path'].rstrip('/')}/{INDEX_DIR_NAME}/{INDEX_DB_NAME}"
        indexed_children[rel] = _database_fingerprint(db_path)
    return WatchSnapshot(files=indexed_files, child_indexes=indexed_children)


def _iter_source_files(root: Path, boundary_roots: list[Path]) -> list[Path]:
    files: list[Path] = []
    for dir_path, dir_names, file_names in os.walk(root):
        current = Path(dir_path)
        dir_names[:] = [name for name in dir_names if not _should_skip_dir(current / name, boundary_roots)]
        for name in file_names:
            path = current / name
            if path.suffix.lower() in TEXT_EXTENSIONS:
                files.append(path)
    return files


def _child_index_snapshot(root: Path, own_db_path: Path | None, boundary_roots: list[Path]) -> dict[str, tuple[int, ...]]:
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
        snapshot[rel] = _database_fingerprint(direct_db)
    for db_path in _iter_index_databases_outside_boundaries(root, boundary_roots):
        try:
            resolved = db_path.resolve()
            if own_db and resolved == own_db:
                continue
            rel = resolved.relative_to(root).as_posix()
        except (OSError, ValueError):
            continue
        snapshot[rel] = _database_fingerprint(db_path)
    return snapshot


def _iter_index_databases_outside_boundaries(root: Path, boundary_roots: list[Path]) -> list[Path]:
    matches: list[Path] = []
    boundaries = [path.resolve() for path in boundary_roots]
    for dir_path, dir_names, file_names in os.walk(root):
        current = Path(dir_path)
        dir_names[:] = [
            name
            for name in dir_names
            if not _should_skip_index_scan_dir(current / name, boundaries)
        ]
        if current.name == INDEX_DIR_NAME and INDEX_DB_NAME in file_names:
            matches.append(current / INDEX_DB_NAME)
            dir_names[:] = []
    return matches


def _database_fingerprint(db_path: Path) -> tuple[int, ...]:
    values: list[int] = []
    for path in (db_path, Path(f"{db_path}-wal"), Path(f"{db_path}-shm")):
        try:
            stat = path.stat()
            values.extend([stat.st_size, stat.st_mtime_ns])
        except OSError:
            values.extend([0, 0])
    return tuple(values)


def _should_skip_dir(path: Path, boundary_roots: list[Path]) -> bool:
    resolved = path.resolve()
    if any(_is_relative_to(resolved, root) for root in boundary_roots):
        return True
    return any(part in DEFAULT_EXCLUDE_DIRS for part in resolved.parts)


def _should_skip_index_scan_dir(path: Path, boundary_roots: list[Path]) -> bool:
    resolved = path.resolve()
    if any(_is_relative_to(resolved, root) for root in boundary_roots):
        return True
    if resolved.name == INDEX_DIR_NAME:
        return False
    return any(part in DEFAULT_EXCLUDE_DIRS for part in resolved.parts)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
