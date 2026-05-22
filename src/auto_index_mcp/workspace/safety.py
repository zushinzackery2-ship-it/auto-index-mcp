from __future__ import annotations

from pathlib import Path


def ensure_relative_to(path: Path, root: Path, display_path: str) -> Path:
    resolved_path = path.resolve()
    resolved_root = root.resolve()
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(f"path escapes project root: {display_path}") from exc
    return resolved_path

