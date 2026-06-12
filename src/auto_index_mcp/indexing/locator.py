from __future__ import annotations

import os
from pathlib import Path

from ..core.config import DEFAULT_EXCLUDE_DIRS
from ..core._utils import is_relative_to


INDEX_DB_NAME = "index.db"
INDEX_DIR_NAME = ".auto-index-mcp"


def iter_index_databases(root: Path, boundary_roots: list[Path] | None = None) -> list[Path]:
    root = root.resolve()
    boundaries = [path.resolve() for path in boundary_roots or []]
    matches: list[Path] = []
    for dir_path, dir_names, file_names in os.walk(root):
        current = Path(dir_path)
        if _is_boundary(current, boundaries):
            dir_names[:] = []
            continue
        direct_db = current / INDEX_DIR_NAME / INDEX_DB_NAME
        if direct_db.exists():
            matches.append(direct_db)
            if current != root:
                dir_names[:] = []
                continue
            dir_names[:] = [
                name
                for name in dir_names
                if name != INDEX_DIR_NAME and not _should_skip_dir(current / name, boundaries)
            ]
            continue
        dir_names[:] = [name for name in dir_names if not _should_skip_dir(current / name, boundaries)]
        if current.name == INDEX_DIR_NAME and INDEX_DB_NAME in file_names:
            matches.append(current / INDEX_DB_NAME)
            dir_names[:] = []
    return matches


def _should_skip_dir(path: Path, boundary_roots: list[Path]) -> bool:
    resolved = path.resolve()
    if _is_boundary(resolved, boundary_roots):
        return True
    if resolved.name == INDEX_DIR_NAME:
        return False
    return any(part in DEFAULT_EXCLUDE_DIRS for part in resolved.parts)


def _is_boundary(path: Path, boundary_roots: list[Path]) -> bool:
    return any(is_relative_to(path, boundary) for boundary in boundary_roots)
