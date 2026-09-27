"""Shared utility functions used across multiple modules."""

from __future__ import annotations

from pathlib import Path


def is_relative_to(path: Path, root: Path) -> bool:
    """Check if path is relative to root (i.e., path starts with root)."""
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
