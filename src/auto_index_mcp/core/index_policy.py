from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import INDEX_VERSION
from ..indexing.store import IndexStore


def can_reuse_index(store: IndexStore | None, root: Path | None) -> bool:
    if store is None or root is None:
        return False
    metadata = store.get_metadata_map()
    return (
        metadata.get("root") == str(root)
        and metadata.get("updated_at") is not None
        and int(metadata.get("version") or 0) == INDEX_VERSION
    )


def can_start_auto_watch(store: IndexStore | None, root: Path | None, result: dict[str, Any] | None) -> bool:
    if result is not None and result.get("status") == "build-lock-timeout":
        return False
    return can_reuse_index(store, root)
