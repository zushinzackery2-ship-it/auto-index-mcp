"""Maintenance scans enumerate all caches with memory bounded by tree depth."""
import os
from pathlib import Path
import time

from .inventory import is_link


def cache_directories(base: Path, deadline=float("inf")):
    stack = [_visit(Path(base).resolve(), deadline)]
    try:
        while stack:
            current = next(stack[-1], None)
            if current is None:
                stack.pop().close()
            elif current[1]:
                yield current[0]
            else:
                stack.append(_visit(current[0], deadline))
    finally:
        for iterator in stack:
            iterator.close()


def _visit(current, deadline):
    _check_time(deadline)
    if (current / "marker.json").is_file():
        from ..registry.markers import read_index_marker
        marker = read_index_marker(current)
        if marker and marker.get("tool") == "auto-index-mcp":
            yield current, True
            return
    inline = current / ".auto-index-mcp"
    if inline.is_dir() and not is_link(inline):
        if any((inline / name).exists() for name in ("marker.json", "index.db", "recovery")) or any(inline.glob("*.corrupt-*")):
            yield inline, True
    with os.scandir(current) as entries:
        for entry in entries:
            _check_time(deadline)
            if (entry.name not in (".git", ".hg", ".svn", ".auto-index-mcp")
                    and entry.is_dir(follow_symlinks=False) and not is_link(Path(entry.path))):
                yield Path(entry.path), False


def _check_time(deadline):
    if time.monotonic() >= deadline:
        raise TimeoutError("cache discovery deadline expired")
