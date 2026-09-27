from __future__ import annotations

import fnmatch
from pathlib import Path

from ..workspace.source import source_path

MAX_RG_COMMAND_CHARS = 24_000


def indexed_targets(root: Path, files: list[dict], pattern: str | None) -> list[tuple[Path, str]]:
    return [
        (source_path(root, item).resolve(), item["path"])
        for item in files
        if not pattern or fnmatch.fnmatch(item["path"], pattern) or fnmatch.fnmatch(Path(item["path"]).name, pattern)
    ]


def target_batches(command: list[str], targets: list[tuple[Path, str]]):
    current: list[tuple[Path, str]] = []
    base_size = sum(len(part.encode("utf-8")) + 3 for part in command)
    size = base_size
    for target in targets:
        length = len(str(target[0]).encode("utf-8")) + 3
        if current and size + length > MAX_RG_COMMAND_CHARS:
            yield current
            current, size = [], base_size
        current.append(target)
        size += length
    if current:
        yield current
