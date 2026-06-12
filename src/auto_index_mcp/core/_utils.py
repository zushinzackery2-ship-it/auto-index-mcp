"""Shared utility functions used across multiple modules."""

from __future__ import annotations

import re
from pathlib import Path


def is_relative_to(path: Path, root: Path) -> bool:
    """Check if path is relative to root (i.e., path starts with root)."""
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def strip_comments(line: str) -> str:
    """Strip Python and C++ style comments from a line."""
    return line.split("#", 1)[0].split("//", 1)[0]


def strip_string_literals(line: str) -> str:
    """Remove string literals (single, double, backtick) from a line.

    Used for brace counting in multi-language code analysis.
    """
    return re.sub(r"(['\"`]).*?\1", "", line)