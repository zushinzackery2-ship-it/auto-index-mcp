"""Normalization of caller-supplied file paths.

AI callers hand over paths in whatever shape their context contains: Windows
backslashes, absolute paths copied from terminal output, quoted strings, or
"./"-prefixed fragments. The index stores project-relative forward-slash
paths, so every path-taking lookup funnels through here first instead of
failing on an exact-string mismatch.
"""

from __future__ import annotations

import os
from pathlib import Path


def normalize_input_path(path: str, root: Path | None = None) -> str:
    """Best-effort conversion into project-relative forward-slash form.

    Handles surrounding quotes/whitespace, backslashes, duplicate slashes,
    ``./`` prefixes and absolute paths that point inside ``root``. Absolute
    paths outside ``root`` are returned cleaned but unchanged so the caller
    can produce a precise error.
    """
    cleaned = path.strip().strip("'\"").strip()
    cleaned = cleaned.replace("\\", "/")
    while "//" in cleaned:
        cleaned = cleaned.replace("//", "/")
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    if root is not None and is_absolute_like(cleaned):
        relative = relativize_to_root(cleaned, root)
        if relative is not None:
            return relative
    if is_absolute_like(cleaned):
        return cleaned.rstrip("/")
    return cleaned.strip("/")


def is_absolute_like(path: str) -> bool:
    """True for POSIX-absolute or Windows drive-letter paths."""
    if path.startswith("/"):
        return True
    return len(path) >= 2 and path[1] == ":" and path[0].isalpha()


def relativize_to_root(path: str, root: Path) -> str | None:
    """Project-relative form of an absolute path under ``root``, else None.

    Comparison folds case on Windows so ``d:/proj/src`` matches ``D:/proj``.
    """
    try:
        resolved = Path(path).resolve(strict=False)
        resolved_root = root.resolve(strict=False)
    except (OSError, ValueError):
        return None
    path_parts = resolved.parts
    root_parts = resolved_root.parts
    if len(path_parts) < len(root_parts):
        return None
    fold = str.lower if os.name == "nt" else str
    if tuple(fold(part) for part in path_parts[: len(root_parts)]) != tuple(
        fold(part) for part in root_parts
    ):
        return None
    return "/".join(path_parts[len(root_parts):])
