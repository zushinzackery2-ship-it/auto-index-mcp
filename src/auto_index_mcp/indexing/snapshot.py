from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ..core.config import TEXT_EXTENSIONS
from ..core.ignore_rules import IgnoreRules
from ..core._utils import is_relative_to
from .snapshot_child import (
    child_index_snapshot,
    direct_child_index_db,
    indexed_child_snapshot,
    is_index_related_path,
    is_own_database_path,
    is_under_boundary,
    subtree_had_child_index,
)


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
    ignore_patterns: list[str] | None = None,
) -> WatchSnapshot:
    root = root.resolve()
    boundaries = [path.resolve() for path in boundary_roots or []]
    ignore_rules = IgnoreRules.from_root(root, ignore_patterns)
    files: dict[str, tuple[int, int]] = {}
    for path in _iter_source_files(root, boundaries, ignore_rules):
        try:
            stat = path.stat()
            rel = path.resolve().relative_to(root).as_posix()
        except (OSError, ValueError):
            continue
        files[rel] = (stat.st_size, stat.st_mtime_ns)
    return WatchSnapshot(
        files=files,
        child_indexes=child_index_snapshot(root, own_db_path, boundaries, ignore_rules),
    )


def update_watch_snapshot(
    root: Path,
    previous: WatchSnapshot,
    changed_paths: set[Path],
    boundary_roots: list[Path] | None = None,
    own_db_path: Path | None = None,
    ignore_patterns: list[str] | None = None,
) -> WatchSnapshot:
    root = root.resolve()
    boundaries = [path.resolve() for path in boundary_roots or []]
    ignore_rules = IgnoreRules.from_root(root, ignore_patterns)
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
            return take_watch_snapshot(root, boundaries, own_db_path, ignore_patterns)
        if is_own_database_path(path, own_db):
            continue
        if is_under_boundary(path, boundaries):
            refresh_child_indexes = True
            continue
        if is_index_related_path(path):
            refresh_child_indexes = True
            continue
        if path.exists() and path.is_dir():
            if direct_child_index_db(path, own_db) is not None:
                _remove_entries_under(files, rel)
                refresh_child_indexes = True
                continue
            if _should_skip_dir(path, boundaries, ignore_rules):
                _remove_entries_under(files, rel)
                continue
            _replace_subtree(files, root, path, rel, boundaries, ignore_rules)
            refresh_child_indexes = refresh_child_indexes or subtree_had_child_index(previous.child_indexes, rel)
            continue
        if path.exists() and path.is_file() and _is_indexable_source(path, boundaries, ignore_rules):
            try:
                stat = path.stat()
            except OSError:
                files.pop(rel, None)
            else:
                files[rel] = (stat.st_size, stat.st_mtime_ns)
            continue
        files.pop(rel, None)
        _remove_entries_under(files, rel)
        refresh_child_indexes = refresh_child_indexes or subtree_had_child_index(previous.child_indexes, rel)
    child_indexes = (
        child_index_snapshot(root, own_db_path, boundaries, ignore_rules)
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
    indexed_children = indexed_child_snapshot(root, child_indexes)
    return WatchSnapshot(files=indexed_files, child_indexes=indexed_children)


def _iter_source_files(
    root: Path,
    boundary_roots: list[Path],
    ignore_rules: IgnoreRules,
) -> list[Path]:
    files: list[Path] = []
    for dir_path, dir_names, file_names in os.walk(root):
        current = Path(dir_path)
        dir_names[:] = [
            name
            for name in dir_names
            if not _should_skip_dir(current / name, boundary_roots, ignore_rules)
        ]
        for name in file_names:
            path = current / name
            if _is_indexable_source(path, boundary_roots, ignore_rules):
                files.append(path)
    return files


def _replace_subtree(
    files: dict[str, tuple[int, int]],
    root: Path,
    path: Path,
    rel: str,
    boundary_roots: list[Path],
    ignore_rules: IgnoreRules,
) -> None:
    _remove_entries_under(files, rel)
    for source in _iter_source_files(path, boundary_roots, ignore_rules):
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


def _should_skip_dir(
    path: Path,
    boundary_roots: list[Path],
    ignore_rules: IgnoreRules,
) -> bool:
    resolved = path.resolve()
    if any(is_relative_to(resolved, root) for root in boundary_roots):
        return True
    return ignore_rules.should_prune_dir(resolved)


def _is_indexable_source(
    path: Path,
    boundary_roots: list[Path],
    ignore_rules: IgnoreRules,
) -> bool:
    return (
        path.suffix.lower() in TEXT_EXTENSIONS
        and not ignore_rules.is_ignored(path, is_dir=False)
        and not _should_skip_dir(path.parent, boundary_roots, ignore_rules)
    )
