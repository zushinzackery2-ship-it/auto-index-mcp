"""Persisted lock locations keep coordination files out of disposable data."""
import re
from pathlib import Path
from ..runtime.files.json import read_json

COORDINATION_FILES = (
    "sessions.lock", "maintenance.lock", "watcher.lock", "index.build.lock",
    "embeddings.build.lock", "index.schema.lock", "embeddings.schema.lock",
)


def lock_directory(directory: Path) -> Path:
    directory = Path(directory).resolve()
    try:
        marker = read_json(directory / "marker.json")
    except (FileNotFoundError, ValueError):
        return directory
    if not isinstance(marker, dict) or marker.get("tool") != "auto-index-mcp" or not marker.get("lock_dir"):
        return directory
    location = Path(marker["lock_dir"])
    identity = str(marker.get("index_id", ""))
    if not location.is_absolute() or location.name != identity or not re.fullmatch(r"[a-f0-9]{32}", identity) or location.parent.name != "locks":
        raise ValueError(f"invalid cache coordination location: {directory}")
    return location


def lock_path(path: Path) -> Path:
    return lock_directory(path.parent) / path.name if path.name in COORDINATION_FILES else path
