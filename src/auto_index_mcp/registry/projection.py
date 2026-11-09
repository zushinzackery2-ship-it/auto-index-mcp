"""Read-only path projections make moved-cache previews match actual cleanup."""
from pathlib import Path

from ..cache.identity import directory_identity
from ..storage.metadata import DEFAULT_METADATA_READER
from ..domain.config import INDEX_DIR_NAMES
from .markers import is_safe_to_delete, read_index_marker
from .paths import registry_key


def projected_entries(entries, roots) -> dict:
    projected = dict(entries)
    for target in roots:
        root = Path(target).resolve()
        if not root.is_dir():
            continue
        identity = directory_identity(root)
        for key, entry in entries.items():
            if entry.get("root_identity") == identity:
                projected[key] = dict(entry, root=str(root))
        for name in INDEX_DIR_NAMES:
            directory = root / name
            if directory.is_dir():
                _project_inline(projected, root, directory)
    return projected


def _project_inline(entries, root, directory) -> None:
    marker = read_index_marker(directory) or dict()
    previous = marker.get("root") or DEFAULT_METADATA_READER.read_metadata(directory / "index.db").get("root")
    if not previous or not is_safe_to_delete(directory, previous):
        return
    key = registry_key(directory)
    entry = entries.get(key) or next((row for row in entries.values()
                                     if marker.get("index_id") and row.get("index_id") == marker["index_id"]), dict())
    entries[key] = dict(entry, root=str(root), index_dir=str(directory),
                        last_attached_at=entry.get("last_attached_at", marker.get("created_at", 0)),
                        ephemeral=False, source=entry.get("source", "scan"))
