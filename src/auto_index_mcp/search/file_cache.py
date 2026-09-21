from __future__ import annotations

import sys
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any

from ..core.text_decode import read_text_file
from ..workspace.safety import ensure_relative_to

MAX_CACHE_BYTES = 8 * 1024 * 1024
_FILE_CONTENT_CACHE: OrderedDict[str, tuple[int, int, list[str], int]] = OrderedDict()
_CACHE_LOCK = threading.Lock()
_cache_bytes = 0


def source_path(root: Path, item: dict[str, Any]) -> Path:
    source_root = Path(item.get("source_root") or root)
    return ensure_relative_to(source_root / item.get("source_path", item["path"]), source_root, item["path"])


def cached_read_lines(root: Path, item: dict[str, Any]) -> list[str]:
    """LRU bounded by actual Python bytes, validated by mtime and size."""
    global _cache_bytes
    path = source_path(root, item)
    key = str(path)
    stat = path.stat()
    with _CACHE_LOCK:
        cached = _FILE_CONTENT_CACHE.get(key)
        if cached is not None and cached[:2] == (stat.st_mtime_ns, stat.st_size):
            _FILE_CONTENT_CACHE.move_to_end(key)
            return cached[2]
    lines = read_text_file(path).splitlines()
    size = sys.getsizeof(lines) + sum(sys.getsizeof(line) for line in lines) + sys.getsizeof(key)
    with _CACHE_LOCK:
        old = _FILE_CONTENT_CACHE.pop(key, None)
        if old is not None:
            _cache_bytes -= old[3]
        if size <= MAX_CACHE_BYTES:
            while _FILE_CONTENT_CACHE and _cache_bytes + size > MAX_CACHE_BYTES:
                _, evicted = _FILE_CONTENT_CACHE.popitem(last=False)
                _cache_bytes -= evicted[3]
            _FILE_CONTENT_CACHE[key] = (stat.st_mtime_ns, stat.st_size, lines, size)
            _cache_bytes += size
    return lines


def clear_file_cache() -> None:
    global _cache_bytes
    with _CACHE_LOCK:
        _FILE_CONTENT_CACHE.clear()
        _cache_bytes = 0
