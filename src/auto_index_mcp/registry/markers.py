from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ..domain.config import INDEX_DIR_NAMES
from ..storage.metadata import DEFAULT_METADATA_READER
from .paths import CACHEDIR_TAG_CONTENT, CACHEDIR_TAG_NAME, MARKER_FILE_NAME, TOOL_NAME, registry_key
from ..cache.identity import directory_identity, identity_key
from ..cache.layout import cache_directory
from ..cache.locking import COORDINATION_FILES
from ..runtime.leases import BuildLock
from ..runtime.files.json import atomic_json, read_json


def write_index_markers(index_dir: Path | str, root: Path | str) -> None:
    """Publish ownership and lock routing before the first session attaches."""
    directory = Path(index_dir)
    directory.mkdir(parents=True, exist_ok=True)
    marker = directory / MARKER_FILE_NAME
    if not marker.exists():
        identity = identity_key(directory)
        gate = BuildLock(cache_directory() / "locks" / identity / "marker.publish.lock", resolve_cache=False)
        if not gate.acquire(3.0):
            raise TimeoutError(f"cache ownership publication is busy: {directory}")
        try:
            if not marker.exists():
                data = dict(tool=TOOL_NAME, root=str(Path(root).resolve()), created_at=time.time(),
                            index_id=identity, root_identity=directory_identity(Path(root)))
                if not (directory / "index.db").exists() and not any((directory / name).exists() for name in COORDINATION_FILES):
                    data["lock_dir"] = str(cache_directory() / "locks" / identity)
                atomic_json(marker, data)
        finally:
            gate.release()
    existing = read_index_marker(directory)
    if existing is None or existing.get("tool") != TOOL_NAME:
        raise ValueError(f"cache ownership could not be verified: {directory}")
    tag = directory / CACHEDIR_TAG_NAME
    if not tag.exists():
        tag.write_text(CACHEDIR_TAG_CONTENT, encoding="ascii")
    if directory.name in INDEX_DIR_NAMES and not (directory / ".gitignore").exists():
        (directory / ".gitignore").write_text("*\n", encoding="ascii")


def rebind_marker(index_dir, root, copied=False) -> None:
    directory = Path(index_dir)
    marker = read_index_marker(directory)
    if marker is None or marker.get("tool") != TOOL_NAME:
        raise ValueError(f"cache ownership could not be verified: {directory}")
    marker.update(root=str(Path(root).resolve()), root_identity=directory_identity(Path(root)))
    if copied:
        marker["index_id"] = identity_key(directory)
        marker["lock_dir"] = str(cache_directory() / "locks" / marker["index_id"])
    atomic_json(directory / MARKER_FILE_NAME, marker)


def read_index_marker(index_dir: Path | str) -> dict[str, Any] | None:
    try:
        raw = read_json(Path(index_dir) / MARKER_FILE_NAME)
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
    if (directory / MARKER_FILE_NAME).exists() and (marker is None or marker.get("tool") != TOOL_NAME):
        return False
    marker_ok = (
        marker is not None
        and marker.get("tool") == TOOL_NAME
        and _same_root(marker.get("root"), expected_root)
    )
    if directory.name not in INDEX_DIR_NAMES:
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
