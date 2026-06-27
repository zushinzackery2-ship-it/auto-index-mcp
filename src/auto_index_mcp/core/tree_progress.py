from __future__ import annotations

import threading
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class _FolderState:
    complete: bool = False
    child_dirs: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class _TreeFile:
    path: str
    name: str
    parent: str
    language: str


class TreeProgress:
    """In-memory directory progress for tree_get while the main index is building."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._root: Path | None = None
        self._active = False
        self._files: dict[str, _TreeFile] = {}
        self._folders: dict[str, _FolderState] = {}

    def start(self, root: Path) -> None:
        with self._lock:
            self._root = root.resolve()
            self._active = True
            self._files = {}
            self._folders = {"": _FolderState()}

    def clear(self) -> None:
        with self._lock:
            self._root = None
            self._active = False
            self._files = {}
            self._folders = {}

    def finish(self) -> None:
        with self._lock:
            self._active = False

    def note_directory(self, path: Path, child_dirs: list[Path]) -> None:
        with self._lock:
            rel = self._relative(path)
            state = self._folders.setdefault(rel, _FolderState())
            for child in child_dirs:
                child_rel = self._relative(child)
                state.child_dirs.add(child_rel)
                self._folders.setdefault(child_rel, _FolderState())

    def finish_directory(self, path: Path) -> None:
        with self._lock:
            self._folders.setdefault(self._relative(path), _FolderState()).complete = True

    def note_file(self, path: str, name: str, parent: str, language: str) -> None:
        with self._lock:
            self._files[path] = _TreeFile(path, name, parent, language)

    def snapshot(self, root_path: str = "", depth: int = 2, limit: int = 120) -> dict[str, Any] | None:
        with self._lock:
            if self._root is None or not self._folders:
                return None
            files = list(self._files.values())
            folders = {key: _FolderState(value.complete, set(value.child_dirs)) for key, value in self._folders.items()}
            scan_active = self._active

        root = _clean_path(root_path)
        folder_rows = _folder_rows(files, folders, root, max(1, depth), limit)
        return {
            "format": "auto_index_tree_partial",
            "root": root_path,
            "folders": folder_rows,
            "partial": True,
            "tree_scan_active": scan_active,
            "requested_depth": depth,
            "completed_depth": _completed_depth(folders, root, max(0, depth)),
        }

    def _relative(self, path: Path) -> str:
        if self._root is None:
            return ""
        resolved = path.resolve(strict=False)
        if resolved == self._root:
            return ""
        return str(resolved.relative_to(self._root)).replace("\\", "/")


def _folder_rows(
    files: list[_TreeFile],
    folders: dict[str, _FolderState],
    root: str,
    depth: int,
    limit: int,
) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = defaultdict(lambda: {"file_count": 0, "languages": Counter(), "samples": []})
    pending: set[str] = set()
    for item in files:
        if not _under_root(item.path, root):
            continue
        key = _folder_key(item.parent, depth)
        data = grouped[key]
        data["file_count"] += 1
        data["languages"][item.language] += 1
        if len(data["samples"]) < 5:
            data["samples"].append(item.name)
    for folder, state in folders.items():
        if not _under_root(folder, root):
            continue
        key = _folder_key(folder, depth)
        grouped.setdefault(key, {"file_count": 0, "languages": Counter(), "samples": []})
        if not state.complete:
            pending.add(key)
    rows = []
    for folder, data in sorted(grouped.items())[:limit]:
        row = {
            "folder": folder,
            "file_count": data["file_count"],
            "languages": dict(data["languages"]),
            "samples": data["samples"],
        }
        if folder in pending:
            row["state"] = "indexing"
            row["message"] = "inner is indexing"
        rows.append(row)
    return rows


def _completed_depth(folders: dict[str, _FolderState], root: str, requested_depth: int) -> int:
    completed = 0
    for level in range(requested_depth + 1):
        if any(
            _relative_depth(folder, root) is not None
            and _relative_depth(folder, root) <= level
            and not state.complete
            for folder, state in folders.items()
        ):
            break
        completed = level
    return completed


def _folder_key(folder: str, depth: int) -> str:
    if not folder:
        return "."
    parts = folder.split("/")
    return "/".join(parts[:depth])


def _under_root(path: str, root: str) -> bool:
    if not root:
        return True
    return path == root or path.startswith(root + "/")


def _relative_depth(path: str, root: str) -> int | None:
    if not _under_root(path, root):
        return None
    if not root:
        return 0 if not path else len(path.split("/"))
    if path == root:
        return 0
    return len(path[len(root) + 1:].split("/"))


def _clean_path(path: str) -> str:
    return path.replace("\\", "/").strip("/")
