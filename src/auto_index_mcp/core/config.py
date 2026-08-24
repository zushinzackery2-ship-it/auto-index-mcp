from __future__ import annotations

import os
from pathlib import Path

INDEX_VERSION = 7
DEFAULT_WATCH_DEBOUNCE_SECONDS = 0.25
DEFAULT_ENABLE_REBUILD_WAIT_SECONDS = 3.0
DEFAULT_MAX_SOURCE_BYTES = 2 * 1024 * 1024

DEFAULT_EXCLUDE_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".auto-index-mcp",
    ".claude",
    ".tox",
    ".venv",
    "__pycache__",
    "bin",
    "build",
    "dist",
    "models",
    "node_modules",
    "obj",
    "target",
    "third-party",
}

DEFAULT_EXCLUDE_FILE_PATTERNS = {
    "*.db",
    "*.dll",
    "*.dylib",
    "*.exe",
    "*.lib",
    "*.obj",
    "*.pdb",
    "*.png",
    "*.pyc",
    "*.so",
    "*.zip",
}

LANGUAGE_BY_EXTENSION = {
    ".c": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cs": "csharp",
    ".css": "css",
    ".go": "go",
    ".h": "cpp",
    ".hpp": "cpp",
    ".html": "html",
    ".java": "java",
    ".js": "javascript",
    ".json": "json",
    ".jsx": "javascript",
    ".kt": "kotlin",
    ".md": "markdown",
    ".pas": "pascal",
    ".py": "python",
    ".rs": "rust",
    ".toml": "toml",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".txt": "text",
    ".xml": "xml",
    ".yaml": "yaml",
    ".yml": "yaml",
}

TEXT_EXTENSIONS = set(LANGUAGE_BY_EXTENSION)


def project_index_root(root: Path) -> Path:
    return root / ".auto-index-mcp"


REGISTRY_DIR_ENV = "AUTO_INDEX_REGISTRY_DIR"


def registry_directory() -> Path:
    """Per-user state directory holding the index registry.

    Resolution order: ``AUTO_INDEX_REGISTRY_DIR`` (test isolation and
    explicit overrides), ``%LOCALAPPDATA%`` on Windows, then
    ``$XDG_STATE_HOME`` with the ``~/.local/state`` fallback on POSIX.
    """
    override = os.environ.get(REGISTRY_DIR_ENV, "").strip()
    if override:
        return Path(override)
    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
        base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
        return base / "auto-index-mcp"
    xdg_state = os.environ.get("XDG_STATE_HOME", "").strip()
    base = Path(xdg_state) if xdg_state else Path.home() / ".local" / "state"
    return base / "auto-index-mcp"
