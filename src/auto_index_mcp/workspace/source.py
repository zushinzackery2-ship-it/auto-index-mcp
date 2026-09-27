"""Bounded source reads with explicit freshness and truncation metadata."""
from __future__ import annotations

import codecs
from pathlib import Path

from ..languages.text_decode import TEXT_ENCODINGS
from ..domain.errors import ServiceError
from .safety import ensure_relative_to

MAX_SOURCE_BYTES = 2 * 1024 * 1024
MAX_BODY_BYTES = 64 * 1024
MAX_CONTEXT_BYTES = 32 * 1024


def clip_utf8(text: str, limit: int) -> str:
    return text.encode("utf-8")[:max(0, limit)].decode("utf-8", errors="ignore")


def source_path(root: Path, item: dict) -> Path:
    source_root = Path(item.get("source_root") or root).resolve()
    target = source_root / item.get("source_path", item["path"])
    return ensure_relative_to(target, source_root, item["path"])


def read_source(root: Path, item: dict, *, require_fresh: bool = False,
                read_limit: int = MAX_SOURCE_BYTES) -> tuple[list[str], dict]:
    target = source_path(root, item)
    try:
        before = target.stat()
        if require_fresh and (before.st_size != item.get("size") or before.st_mtime_ns != item.get("mtime_ns")):
            raise _drift(item["path"])
        with target.open("rb") as stream:
            raw = stream.read(max(0, read_limit) + 1)
        after = target.stat()
    except FileNotFoundError as exc:
        raise _drift(item["path"]) from exc
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise _drift(item["path"])
    truncated = len(raw) > read_limit
    raw = raw[:read_limit]
    lines = _decode_prefix(raw, truncated).splitlines()
    return lines, dict(source_bytes=before.st_size, read_bytes=len(raw), source_truncated=truncated)


def source_slice(lines: list[str], start: int, end: int, budget: int) -> tuple[str, int, bool]:
    code = "\n".join(lines[max(0, start - 1):end])
    clipped = clip_utf8(code, budget)
    returned_end = start + len(clipped.splitlines()) - 1
    return clipped, returned_end, len(clipped) != len(code) or end > len(lines)


def _decode_prefix(raw: bytes, truncated: bool) -> str:
    error = None
    for encoding in TEXT_ENCODINGS:
        try:
            return codecs.getincrementaldecoder(encoding)().decode(raw, final=not truncated)
        except UnicodeError as exc:
            error = exc
    raise ServiceError("source-encoding", str(error), "check the source file encoding")


def _drift(path: str) -> ServiceError:
    return ServiceError("source-changed", f"source differs from the indexed revision: {path}",
                        "wait for the watcher or call auto_index_manage(action='rebuild')", path=path)
