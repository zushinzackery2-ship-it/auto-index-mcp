"""Registry storage, ownership markers and leased cleanup."""
from .cleanup import build_lock_active, index_dir_size, remove_index_dir
from .markers import is_safe_to_delete, read_index_marker, write_index_markers
from .paths import CACHEDIR_TAG_NAME, MARKER_FILE_NAME, registry_key
from .store import IndexRegistry

__all__ = [
    "CACHEDIR_TAG_NAME", "MARKER_FILE_NAME", "IndexRegistry", "registry_key",
    "build_lock_active", "index_dir_size", "remove_index_dir", "is_safe_to_delete",
    "read_index_marker", "write_index_markers",
]
