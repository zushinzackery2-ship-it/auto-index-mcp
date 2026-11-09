"""Preserve cache placement while isolating the optional child-mounting mode."""
import os
from pathlib import Path

from .identity import identity_key
from ..domain.config import INDEX_DIR_NAMES


def cache_directory() -> Path:
    override = os.environ.get("AUTO_INDEX_CACHE_DIR", "").strip()
    if override:
        return Path(override).resolve()
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA", "").strip()
        base = Path(local) if local else Path.home() / "AppData" / "Local"
    else:
        cache_home = os.environ.get("XDG_CACHE_HOME", "").strip()
        base = Path(cache_home) if cache_home else Path.home() / ".cache"
    return (base / "auto-index-mcp").resolve()


def project_cache(root: Path, mount_child_indexes: bool = False, override: Path | None = None) -> Path:
    mode = os.environ.get("AUTO_INDEX_CACHE_MODE", "").strip().lower()
    if mode not in ("", "user", "project"):
        raise ValueError("AUTO_INDEX_CACHE_MODE must be user or project")
    root = root.resolve()
    legacy = root / ".auto-index-mcp"
    if override is not None:
        directory = Path(override).resolve()
    elif mode == "project" or (not mode and any((root / name / marker).exists()
                                              for name in INDEX_DIR_NAMES for marker in ("index.db", "marker.json"))):
        directory = legacy
    else:
        directory = cache_directory() / "projects" / identity_key(root)
    if mount_child_indexes:
        directory = directory.with_name(directory.name + "-mounted")
    return directory
