from __future__ import annotations

import shutil
from pathlib import Path


def resolve_lsp_executable(name: str, root: Path | None = None) -> str | None:
    roots = _candidate_roots(root)
    for tool_root in roots:
        bundled = _bundled_executable(name, tool_root)
        if bundled:
            return str(bundled)
    for tool_root in roots:
        local = _local_executable(name, tool_root)
        if local:
            return str(local)
    return shutil.which(name)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _candidate_roots(root: Path | None) -> tuple[Path, ...]:
    repo_root = _repo_root()
    if root is None or root == repo_root:
        return (repo_root,)
    return (repo_root, root)


def _bundled_executable(name: str, root: Path) -> Path | None:
    if name != "clangd":
        return None
    candidates = (
        root / "third-party" / "clangd_22.1.0" / "bin" / "clangd.exe",
        root / "third-party" / "clangd_22.1.0" / "bin" / "clangd",
    )
    return _first_existing(candidates)


def _local_executable(name: str, root: Path) -> Path | None:
    candidates = (
        root / ".venv" / "Scripts" / f"{name}.exe",
        root / ".venv" / "Scripts" / f"{name}.cmd",
        root / ".venv" / "Scripts" / name,
        root / ".venv" / "bin" / name,
        root / ".auto-index-mcp" / "lsp" / "npm" / "node_modules" / ".bin" / f"{name}.cmd",
        root / ".auto-index-mcp" / "lsp" / "npm" / "node_modules" / ".bin" / name,
    )
    return _first_existing(candidates)


def _first_existing(candidates: tuple[Path, ...]) -> Path | None:
    for path in candidates:
        if path.exists():
            return path
    return None
