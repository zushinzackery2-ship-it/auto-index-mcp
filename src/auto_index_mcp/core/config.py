from __future__ import annotations

from pathlib import Path

INDEX_VERSION = 1

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
