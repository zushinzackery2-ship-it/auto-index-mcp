from __future__ import annotations

import os
from pathlib import Path

from ..core.ignore_rules import IgnoreRules


INDEX_DB_NAME = "index.db"
INDEX_DIR_NAME = ".auto-index-mcp"


def iter_index_databases(
    root: Path,
    boundary_roots: list[Path] | None = None,
    ignore_patterns: list[str] | None = None,
) -> list[Path]:
    root = root.resolve()
    boundaries = [path.resolve() for path in boundary_roots or []]
    ignore_rules = IgnoreRules.from_root(root, ignore_patterns)
    matches: list[Path] = []

    for dir_path, dir_names, file_names in os.walk(root):
        current = Path(dir_path)
        if _is_boundary(current, boundaries):
            dir_names[:] = []
            continue

        direct_db = current / INDEX_DIR_NAME / INDEX_DB_NAME
        if direct_db.exists():
            matches.append(direct_db)
            if current.resolve() != root:
                dir_names[:] = []
            else:
                dir_names[:] = _filter_dirs(current, dir_names, boundaries, ignore_rules)
            continue

        if current.name == INDEX_DIR_NAME and INDEX_DB_NAME in file_names:
            matches.append(current / INDEX_DB_NAME)
            dir_names[:] = []
            continue

        dir_names[:] = _filter_dirs(current, dir_names, boundaries, ignore_rules)

    return matches


def _filter_dirs(
    current: Path,
    dir_names: list[str],
    boundary_roots: list[Path],
    ignore_rules: IgnoreRules,
) -> list[str]:
    return [
        name for name in dir_names
        if name != INDEX_DIR_NAME
        and not _should_skip_dir(current / name, boundary_roots, ignore_rules)
    ]


def _should_skip_dir(
    path: Path,
    boundary_roots: list[Path],
    ignore_rules: IgnoreRules,
) -> bool:
    resolved = path.resolve()
    if resolved.name == INDEX_DIR_NAME:
        return False
    if any(_is_relative_to(resolved, boundary) for boundary in boundary_roots):
        return True
    return ignore_rules.should_prune_dir(resolved)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _is_boundary(path: Path, boundary_roots: list[Path]) -> bool:
    return any(_is_relative_to(path, boundary) for boundary in boundary_roots)
