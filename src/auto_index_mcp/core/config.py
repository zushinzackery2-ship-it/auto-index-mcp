from __future__ import annotations

from pathlib import Path

INDEX_VERSION = 5
DEFAULT_WATCH_DEBOUNCE_SECONDS = 0.25
# How long a rebuild waits for a concurrent process to finish building the
# shared index before it falls back to building unsynchronised itself.
DEFAULT_BUILD_LOCK_WAIT_SECONDS = 60.0

DEFAULT_EXCLUDE_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".auto-index-mcp",
    ".tox",
    ".venv",
    "__pycache__",
    "bin",
    "build",
    "dist",
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
