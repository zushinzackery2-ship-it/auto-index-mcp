from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from ..core.text_decode import read_text_file

_MAX_FILE_CACHE_SIZE = 1000
_FILE_CONTENT_CACHE: dict[str, tuple[int, list[str]]] = {}
_CACHE_LOCK = threading.Lock()
_CACHE_ACCESS_ORDER: list[str] = []


def source_path(root: Path, item: dict[str, Any]) -> Path:
    source_root = Path(item.get("source_root") or root)
    return source_root / item.get("source_path", item["path"])


def cached_read_lines(root: Path, item: dict[str, Any]) -> list[str]:
    """Read file content with a small mtime-validated LRU cache."""
    path = source_path(root, item)
    key = str(path.resolve())

    with _CACHE_LOCK:
        cached = _FILE_CONTENT_CACHE.get(key)
        if cached is not None:
            try:
                stat = path.stat()
                if stat.st_mtime_ns == cached[0]:
                    _mark_recent(key)
                    return cached[1]
            except OSError:
                pass

    try:
        stat = path.stat()
        lines = read_text_file(path).splitlines()
    except (OSError, UnicodeDecodeError):
        return []

    with _CACHE_LOCK:
        while len(_FILE_CONTENT_CACHE) >= _MAX_FILE_CACHE_SIZE and _CACHE_ACCESS_ORDER:
            oldest = _CACHE_ACCESS_ORDER.pop(0)
            _FILE_CONTENT_CACHE.pop(oldest, None)
        _FILE_CONTENT_CACHE[key] = (stat.st_mtime_ns, lines)
        _CACHE_ACCESS_ORDER.append(key)
    return lines


def clear_file_cache() -> None:
    with _CACHE_LOCK:
        _FILE_CONTENT_CACHE.clear()
        _CACHE_ACCESS_ORDER.clear()


def _mark_recent(key: str) -> None:
    if key in _CACHE_ACCESS_ORDER:
        _CACHE_ACCESS_ORDER.remove(key)
    _CACHE_ACCESS_ORDER.append(key)
