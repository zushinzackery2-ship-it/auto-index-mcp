"""New projects use the user cache; existing project caches remain attachable."""
import os
from pathlib import Path

from .identity import identity_key


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


def project_cache(root: Path) -> Path:
    mode = os.environ.get("AUTO_INDEX_CACHE_MODE", "").strip().lower()
    if mode not in ("", "user", "project"):
        raise ValueError("AUTO_INDEX_CACHE_MODE must be user or project")
    legacy = root / ".auto-index-mcp"
    if mode == "project" or (not mode and ((legacy / "index.db").exists() or (legacy / "marker.json").exists())):
        return legacy
    return cache_directory() / "projects" / identity_key(root)
