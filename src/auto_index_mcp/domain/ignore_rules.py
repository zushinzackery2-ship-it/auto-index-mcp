from __future__ import annotations

import fnmatch
import hashlib
import os
from functools import lru_cache
from pathlib import Path

from pathspec import GitIgnoreSpec

from .config import DEFAULT_EXCLUDE_DIRS, DEFAULT_EXCLUDE_FILE_PATTERNS


@lru_cache(maxsize=256)
def compile_patterns(patterns: tuple[str, ...]) -> GitIgnoreSpec:
    return GitIgnoreSpec.from_lines(patterns)


class IgnoreRules:
    """Git rules are loaded once per directory for this scan's policy snapshot."""

    def __init__(self, root: Path, gitignore_patterns=None, runtime_patterns=None) -> None:
        self.root = root.resolve()
        self.gitignore_patterns = list(read_gitignore_patterns(self.root) if gitignore_patterns is None else gitignore_patterns)
        self.runtime_patterns = list(runtime_patterns or [])
        self._specs = {"": compile_patterns(tuple(self.gitignore_patterns))}
        self._runtime = compile_patterns(tuple(self.runtime_patterns))
        self._directories: dict[str, bool] = {"": False}

    @classmethod
    def from_root(cls, root: Path, runtime_patterns=None) -> "IgnoreRules":
        return cls(root, runtime_patterns=runtime_patterns)

    def _spec(self, directory: str) -> GitIgnoreSpec:
        if directory not in self._specs:
            patterns = read_gitignore_patterns(self.root / directory)
            self._specs[directory] = compile_patterns(tuple(patterns))
        return self._specs[directory]

    def is_ignored(self, path: Path, is_dir: bool) -> bool:
        try:
            rel = path.resolve().relative_to(self.root).as_posix()
        except (OSError, ValueError):
            return True
        return self.is_ignored_rel(rel, is_dir)

    def is_ignored_rel(self, rel_path: str, is_dir: bool) -> bool:
        rel = rel_path.replace("\\", "/").strip("/")
        if rel in ("", "."):
            return False
        if self._is_default_excluded(rel, is_dir):
            return True
        parts = rel.split("/")
        # A child cannot be re-included while its parent remains excluded.
        for end in range(1, len(parts)):
            parent = "/".join(parts[:end])
            if parent not in self._directories:
                self._directories[parent] = self._match(parent, True)
            if self._directories[parent]:
                return True
        return self._match(rel, is_dir)

    def _match(self, rel: str, is_dir: bool) -> bool:
        parts = rel.split("/")
        suffix = "/" if is_dir else ""
        ignored = False
        for end in range(len(parts)):
            directory = "/".join(parts[:end])
            result = self._spec(directory).check_file("/".join(parts[end:]) + suffix).include
            if result is not None:
                ignored = result
        override = self._runtime.check_file(rel + suffix).include
        return ignored if override is None else override

    def should_prune_dir(self, path: Path) -> bool:
        return self.is_ignored(path, True)

    def ignore_files(self) -> list[str]:
        paths = [".gitignore"]
        for directory, names, files in os.walk(self.root):
            current = Path(directory)
            names[:] = [name for name in names if not self.should_prune_dir(current / name)]
            if current != self.root and ".gitignore" in files:
                paths.append((current / ".gitignore").relative_to(self.root).as_posix())
        return sorted(paths)

    def fingerprint(self, known_paths: list[str] | None = None) -> str:
        digest = hashlib.sha256()
        for kind, values in (("dir", sorted(DEFAULT_EXCLUDE_DIRS)), ("file", sorted(DEFAULT_EXCLUDE_FILE_PATTERNS)),
                             ("runtime", self.runtime_patterns)):
            for value in values:
                digest.update(f"{kind}:{value}\n".encode("utf-8"))
        for path in sorted(known_paths if known_paths is not None else self.ignore_files()):
            patterns = self.gitignore_patterns if path == ".gitignore" else read_gitignore_patterns((self.root / path).parent)
            digest.update(f"path:{path}\n".encode("utf-8"))
            digest.update("\n".join(patterns).encode("utf-8"))
        return digest.hexdigest()

    def status(self, known_paths: list[str] | None = None) -> dict[str, object]:
        return {
            "default_exclude_dirs": sorted(DEFAULT_EXCLUDE_DIRS),
            "default_exclude_file_patterns": sorted(DEFAULT_EXCLUDE_FILE_PATTERNS),
            "gitignore_patterns": self.gitignore_patterns,
            "runtime_patterns": self.runtime_patterns,
            "fingerprint": self.fingerprint(known_paths),
        }

    def _is_default_excluded(self, rel: str, is_dir: bool) -> bool:
        if any(part in DEFAULT_EXCLUDE_DIRS for part in rel.split("/")):
            return True
        return not is_dir and any(fnmatch.fnmatch(Path(rel).name, pattern) or fnmatch.fnmatch(rel, pattern)
                                  for pattern in DEFAULT_EXCLUDE_FILE_PATTERNS)


def read_gitignore_patterns(root: Path) -> list[str]:
    try:
        return (root / ".gitignore").read_text(encoding="utf-8-sig").splitlines()
    except FileNotFoundError:
        return []


def ignore_fingerprint(root: Path, runtime_patterns=None, auto_patterns=None, privileged_patterns=None,
                       known_paths: list[str] | None = None) -> str:
    base = IgnoreRules.from_root(root, runtime_patterns).fingerprint(known_paths)
    if not auto_patterns and not privileged_patterns:
        return base
    digest = hashlib.sha256(base.encode("ascii"))
    for kind, patterns in (("auto", auto_patterns), ("privileged", privileged_patterns)):
        for pattern in patterns or []:
            digest.update(f"{kind}:{pattern}\n".encode("utf-8"))
    return digest.hexdigest()
