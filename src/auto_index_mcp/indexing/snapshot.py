from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ..core.config import DEFAULT_EXCLUDE_DIRS, TEXT_EXTENSIONS
from ..core._utils import is_relative_to
from .locator import INDEX_DB_NAME, INDEX_DIR_NAME, iter_index_databases
from .metadata_reader import DEFAULT_METADATA_READER


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


def update_watch_snapshot(
    root: Path,
    previous: WatchSnapshot,
    changed_paths: set[Path],
    boundary_roots: list[Path] | None = None,
    own_db_path: Path | None = None,
) -> WatchSnapshot:
    root = root.resolve()
    boundaries = [path.resolve() for path in boundary_roots or []]
    own_db = own_db_path.resolve() if own_db_path else None
    files = dict(previous.files)
    refresh_child_indexes = False
    for changed_path in changed_paths:
        try:
            path = changed_path.resolve()
            rel = path.relative_to(root).as_posix()
        except (OSError, ValueError):
            continue
        if rel in {"", "."}:
            return take_watch_snapshot(root, boundaries, own_db_path)
        if _is_own_database_path(path, own_db):
            continue
        if _is_under_boundary(path, boundaries):
            refresh_child_indexes = True
            continue
        if _is_index_related_path(path):
            refresh_child_indexes = True
            continue
        if path.exists() and path.is_dir():
            if _direct_child_index_db(path, own_db) is not None:
                _remove_entries_under(files, rel)
                refresh_child_indexes = True
                continue
            if _should_skip_dir(path, boundaries):
                _remove_entries_under(files, rel)
                continue
            _replace_subtree(files, root, path, rel, boundaries)
            refresh_child_indexes = refresh_child_indexes or _subtree_had_child_index(previous, rel)
            continue
        if path.exists() and path.is_file() and _is_indexable_source(path, boundaries):
            try:
                stat = path.stat()
            except OSError:
                files.pop(rel, None)
            else:
                files[rel] = (stat.st_size, stat.st_mtime_ns)
            continue
        files.pop(rel, None)
        _remove_entries_under(files, rel)
        refresh_child_indexes = refresh_child_indexes or _subtree_had_child_index(previous, rel)
    child_indexes = (
        _child_index_snapshot(root, own_db_path, boundaries)
        if refresh_child_indexes
        else dict(previous.child_indexes)
    )
    return WatchSnapshot(files=files, child_indexes=child_indexes)


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


def _replace_subtree(files: dict[str, tuple[int, int]], root: Path, path: Path, rel: str, boundary_roots: list[Path]) -> None:
    _remove_entries_under(files, rel)
    for source in _iter_source_files(path, boundary_roots):
        try:
            stat = source.stat()
            source_rel = source.resolve().relative_to(root).as_posix()
        except (OSError, ValueError):
            continue
        files[source_rel] = (stat.st_size, stat.st_mtime_ns)


def _remove_entries_under(files: dict[str, tuple[int, int]], rel: str) -> None:
    prefix = rel.rstrip("/") + "/"
    for path in list(files):
        if path == rel or path.startswith(prefix):
            files.pop(path, None)


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
    return iter_index_databases(root, boundary_roots)


def _database_fingerprint(db_path: Path) -> tuple[int, ...]:
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


def _should_skip_dir(path: Path, boundary_roots: list[Path]) -> bool:
    resolved = path.resolve()
    if any(is_relative_to(resolved, root) for root in boundary_roots):
        return True
    return any(part in DEFAULT_EXCLUDE_DIRS for part in resolved.parts)


def _is_indexable_source(path: Path, boundary_roots: list[Path]) -> bool:
    return path.suffix.lower() in TEXT_EXTENSIONS and not _should_skip_dir(path.parent, boundary_roots)


def _is_index_related_path(path: Path) -> bool:
    parts = path.parts
    if INDEX_DIR_NAME not in parts:
        return False
    return path.name in {INDEX_DB_NAME, f"{INDEX_DB_NAME}-wal", f"{INDEX_DB_NAME}-shm"} or INDEX_DB_NAME in parts


def _direct_child_index_db(path: Path, own_db: Path | None) -> Path | None:
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


def _is_own_database_path(path: Path, own_db: Path | None) -> bool:
    if own_db is None:
        return False
    try:
        resolved = path.resolve()
    except OSError:
        return False
    return resolved in {own_db, Path(f"{own_db}-wal"), Path(f"{own_db}-shm")}


def _is_under_boundary(path: Path, boundary_roots: list[Path]) -> bool:
    return any(is_relative_to(path, boundary) for boundary in boundary_roots)


def _subtree_had_child_index(previous: WatchSnapshot, rel: str) -> bool:
    prefix = rel.rstrip("/") + "/"
    return any(path == rel or path.startswith(prefix) for path in previous.child_indexes)
