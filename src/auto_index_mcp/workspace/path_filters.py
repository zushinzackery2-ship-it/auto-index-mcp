from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Any

C_FAMILY_LANGUAGES = {"c", "cpp"}


def filter_indexed_files(
    files: list[dict[str, Any]],
    exclude_paths: list[str] | None = None,
    active_only: bool = False,
) -> list[dict[str, Any]]:
    return [
        item
        for item in files
        if not is_excluded_path(item["path"], exclude_paths)
        and _matches_active_source(item, active_only)
    ]


def is_excluded_path(path: str, exclude_paths: list[str] | None = None) -> bool:
    if not exclude_paths:
        return False
    normalized = path.replace("\\", "/").strip("/")
    name = Path(normalized).name
    for pattern in exclude_paths:
        candidate = pattern.replace("\\", "/").strip("/")
        if not candidate:
            continue
        if fnmatch.fnmatch(normalized, candidate) or fnmatch.fnmatch(name, candidate):
            return True
        if _is_directory_prefix(normalized, candidate):
            return True
    return False


def is_glob_pattern(value: str) -> bool:
    return any(char in value for char in "*?[]")


def _matches_active_source(item: dict[str, Any], active_only: bool) -> bool:
    if not active_only:
        return True
    if item.get("language") not in C_FAMILY_LANGUAGES:
        return True
    return bool(item.get("active_source", True))


def _is_directory_prefix(path: str, pattern: str) -> bool:
    prefix = pattern.rstrip("/")
    if pattern.endswith("/**"):
        prefix = pattern[:-3].rstrip("/")
    if pattern.endswith("/*"):
        prefix = pattern[:-2].rstrip("/")
    return path == prefix or path.startswith(prefix + "/")
