from __future__ import annotations

import shutil
from pathlib import Path


def resolve_lsp_executable(name: str) -> str | None:
    bundled = _bundled_executable(name)
    if bundled:
        return str(bundled)
    return shutil.which(name)


def _bundled_executable(name: str) -> Path | None:
    if name != "clangd":
        return None
    repo_root = Path(__file__).resolve().parents[3]
    candidates = (
        repo_root / "third-party" / "clangd_22.1.0" / "bin" / "clangd.exe",
        repo_root / "third-party" / "clangd_22.1.0" / "bin" / "clangd",
    )
    for path in candidates:
        if path.exists():
            return path
    return None
