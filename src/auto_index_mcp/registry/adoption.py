"""Adopt cache data by actual location and rebind moved project ownership."""
from pathlib import Path
from contextlib import nullcontext

from ..cache.discovery import cache_directories
from ..cache.identity import directory_identity, identity_key
from ..cache.layout import cache_directory
from ..runtime.maintenance import MaintenanceLease
from ..storage.index import IndexStore
from ..domain.config import INDEX_DIR_NAMES
from ..storage.metadata import DEFAULT_METADATA_READER
from .markers import is_safe_to_delete, read_index_marker, rebind_marker, write_index_markers
from .paths import registry_key
from .paths import TOOL_NAME


def adopt_directory(registry, directory: Path, explicit_root: Path | None = None, lease_held=False):
    directory = Path(directory).resolve()
    marker = read_index_marker(directory)
    if (directory / "marker.json").exists() and (marker is None or marker.get("tool") != TOOL_NAME):
        raise ValueError(f"cache ownership could not be verified: {directory}")
    meta = DEFAULT_METADATA_READER.read_metadata(directory / "index.db")
    previous = (marker or {}).get("root") or meta.get("root")
    root = explicit_root or (directory.parent if directory.name in INDEX_DIR_NAMES else Path(previous or directory.parent))
    root = root.resolve()
    entry = registry.get(directory)
    if not previous:
        return None
    if previous and not is_safe_to_delete(directory, previous):
        return None
    copied = bool(marker and marker.get("index_id") and marker["index_id"] != identity_key(directory))
    changed = copied or registry_key(previous) != registry_key(root)
    if not changed:
        if entry and registry_key(entry["root"]) == registry_key(root):
            return None
        return _register(registry, directory, root)
    coordination = cache_directory() / "locks" / identity_key(directory) if copied else None
    lease = nullcontext() if lease_held else MaintenanceLease(directory, "adopt", coordination_directory=coordination)
    with lease:
        marker = read_index_marker(directory)
        if marker is None:
            write_index_markers(directory, root)
        else:
            rebind_marker(directory, root, copied=copied)
        if meta and registry_key(meta.get("root", root)) != registry_key(root):
            _rebind_metadata(directory, Path(meta["root"]), root)
        return _register(registry, directory, root)


def _register(registry, directory, root):
    ephemeral = directory.name not in INDEX_DIR_NAMES and directory.parent.name != "projects"
    if not registry.register(root, directory, "scan", ephemeral, attached=False):
        return None
    registry.prune_moved_aliases(directory)
    return dict(registry.get(directory), key=registry_key(directory))


def _rebind_metadata(directory, previous, root):
    store = IndexStore(directory / "index.db")
    with store.connect() as conn:
        store.set_metadata(conn, "root", str(root))
        for row in conn.execute("SELECT path,root,db_path FROM child_indexes").fetchall():
            child_db = Path(row["db_path"])
            if child_db.is_relative_to(previous):
                child_db = root / child_db.relative_to(previous)
            conn.execute("UPDATE child_indexes SET root=?,db_path=? WHERE path=?",
                         (str(root / row["path"]), str(child_db), row["path"]))


def scan_and_adopt(registry, base):
    adopted = []
    for directory in cache_directories(Path(base)):
        entry = adopt_directory(registry, directory)
        if entry:
            adopted.append(entry)
    return adopted


def reconcile_root(registry, root):
    root = Path(root).resolve()
    if not root.is_dir():
        return
    for name in INDEX_DIR_NAMES:
        inline = root / name
        if inline.is_dir() and ((inline / "marker.json").exists() or (inline / "index.db").exists()):
            adopt_directory(registry, inline, root)
    identity = directory_identity(root)
    for entry in list(registry.entries().values()):
        if entry.get("root_identity") == identity and registry_key(entry["root"]) != registry_key(root):
            directory = Path(entry["index_dir"])
            if directory.is_dir():
                adopt_directory(registry, directory, root)
