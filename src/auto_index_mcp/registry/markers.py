from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from ..indexing.locator import INDEX_DIR_NAME
from ..storage.metadata import DEFAULT_METADATA_READER
from .paths import CACHEDIR_TAG_CONTENT, CACHEDIR_TAG_NAME, MARKER_FILE_NAME, TOOL_NAME, registry_key


def write_index_markers(index_dir: Path | str, root: Path | str) -> None:
    """Drop ``marker.json`` and ``CACHEDIR.TAG`` into a fresh index directory.

    Idempotent and best-effort: existing markers are left untouched and IO
    failures never propagate into enable/build.
    """
    directory = Path(index_dir)
    try:
        directory.mkdir(parents=True, exist_ok=True)
        marker = directory / MARKER_FILE_NAME
        if not marker.exists():
            marker.write_text(
                json.dumps(
                    {
                        "tool": TOOL_NAME,
                        "root": str(Path(root).resolve()),
                        "created_at": time.time(),
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
        tag = directory / CACHEDIR_TAG_NAME
        if not tag.exists():
            tag.write_text(CACHEDIR_TAG_CONTENT, encoding="ascii")
    except OSError:
        pass


def read_index_marker(index_dir: Path | str) -> dict[str, Any] | None:
    try:
        raw = json.loads((Path(index_dir) / MARKER_FILE_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


# ---- cleanup helpers -----------------------------------------------------------


def is_safe_to_delete(index_dir: Path | str, expected_root: Path | str) -> bool:
    """Attribution check run before any deletion.

    A directory named ``.auto-index-mcp`` is deletable when either its
    ``marker.json`` or its ``index.db`` metadata names ``expected_root``.
    A directory under any other name (ephemeral override locations) needs the
    explicit marker - index metadata alone is not enough proof of ownership
    for an arbitrarily named path.
    """
    directory = Path(index_dir)
    marker = read_index_marker(directory)
    marker_ok = (
        marker is not None
        and marker.get("tool") == TOOL_NAME
        and _same_root(marker.get("root"), expected_root)
    )
    if directory.name != INDEX_DIR_NAME:
        return marker_ok
    if marker_ok:
        return True
    meta = DEFAULT_METADATA_READER.read_metadata(directory / "index.db")
    return _same_root(meta.get("root"), expected_root)
def _same_root(candidate: Any, expected: Path | str) -> bool:
    if not candidate:
        return False
    try:
        return registry_key(Path(str(candidate))) == registry_key(expected)
    except (OSError, ValueError):
        return False
