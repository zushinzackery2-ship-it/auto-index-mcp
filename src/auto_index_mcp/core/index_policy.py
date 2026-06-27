from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import INDEX_VERSION
from ..indexing.store import IndexStore


def can_reuse_index(
    store: IndexStore | None,
    root: Path | None,
    ignore_fingerprint: str | None = None,
) -> bool:
    if store is None or root is None:
        return False
    metadata = store.get_metadata_map()
    reusable = (
        metadata.get("root") == str(root)
        and metadata.get("updated_at") is not None
        and int(metadata.get("version") or 0) == INDEX_VERSION
    )
    if not reusable:
        return False
    if ignore_fingerprint is None:
        return True
    return metadata.get("ignore_fingerprint") == ignore_fingerprint


def can_start_auto_watch_policy(
    store: IndexStore | None,
    root: Path | None,
    result: dict[str, Any] | None,
    ignore_fingerprint: str | None = None,
) -> bool:
    # A background rebuild has no reusable index yet; the watcher is started by
    # the build's completion hook instead of racing a second full scan here.
    if result is not None and result.get("status") in ("indexing-in-other-process", "indexing-in-background"):
        return False
    return can_reuse_index(store, root, ignore_fingerprint)
