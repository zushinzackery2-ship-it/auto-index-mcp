from __future__ import annotations

import os
from pathlib import Path

INDEX_VERSION = 11
PARSER_VERSION = 6
DEFAULT_WATCH_DEBOUNCE_SECONDS = 0.25
DEFAULT_ENABLE_REBUILD_WAIT_SECONDS = 3.0
DEFAULT_MAX_SOURCE_BYTES = 2 * 1024 * 1024
INDEX_DB_NAME = "index.db"
INDEX_DIR_NAME = ".auto-index-mcp"
INDEX_DIR_NAMES = (INDEX_DIR_NAME, INDEX_DIR_NAME + "-mounted")

INTERNAL_EXCLUDE_DIRS = set((".git", ".hg", ".svn", *INDEX_DIR_NAMES))

DEFAULT_EXCLUDE_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".auto-index-mcp",
    ".auto-index-mcp-mounted",
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
    ".vcxproj": "xml",
    ".vue": "vue",
    ".svelte": "text",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".yaml": "yaml",
    ".yml": "yaml",
}

TEXT_EXTENSIONS = set(LANGUAGE_BY_EXTENSION)


def text_extensions() -> set[str]:
    extra = os.environ.get("AUTO_INDEX_TEXT_EXTENSIONS", "").split(",")
    normalized = {value.strip().lower() for value in extra if value.strip()}
    if any(not value.startswith(".") or any(char in value for char in "/\\*? ") for value in normalized):
        raise ValueError("AUTO_INDEX_TEXT_EXTENSIONS requires comma-separated extensions such as .sql,.lua")
    return TEXT_EXTENSIONS | normalized


def project_index_root(root: Path, mount_child_indexes: bool = False, override: Path | None = None) -> Path:
    from ..cache.layout import project_cache
    return project_cache(root, mount_child_indexes, override)


REGISTRY_DIR_ENV = "AUTO_INDEX_REGISTRY_DIR"


def control_directory() -> Path:
    override = os.environ.get("AUTO_INDEX_CONTROL_DIR", "").strip()
    return Path(override).resolve() if override else _user_state_directory() / "control"


def registry_directory() -> Path:
    """Per-user state directory holding the index registry.

    Resolution order: ``AUTO_INDEX_REGISTRY_DIR`` (test isolation and
    explicit overrides), ``%LOCALAPPDATA%`` on Windows, then
    ``$XDG_STATE_HOME`` with the ``~/.local/state`` fallback on POSIX.
    """
    override = os.environ.get(REGISTRY_DIR_ENV, "").strip()
    if override:
        return Path(override)
    return _user_state_directory()


def _user_state_directory() -> Path:
    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
        base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
        return base / "auto-index-mcp"
    xdg_state = os.environ.get("XDG_STATE_HOME", "").strip()
    base = Path(xdg_state) if xdg_state else Path.home() / ".local" / "state"
    return base / "auto-index-mcp"
