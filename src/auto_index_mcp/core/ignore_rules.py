from __future__ import annotations

import fnmatch
import hashlib
from dataclasses import dataclass
from pathlib import Path

from .config import DEFAULT_EXCLUDE_DIRS, DEFAULT_EXCLUDE_FILE_PATTERNS
from ._utils import is_relative_to


@dataclass(frozen=True)
class IgnoreRule:
    pattern: str
    negated: bool
    directory_only: bool
    anchored: bool
    has_slash: bool

    @classmethod
    def parse(cls, raw: str) -> "IgnoreRule | None":
        value = raw.strip()
        if not value or value.startswith("#"):
            return None
        negated = value.startswith("!")
        if negated:
            value = value[1:].strip()
        if not value:
            return None
        anchored = value.startswith("/")
        value = value.lstrip("/")
        directory_only = value.endswith("/")
        value = value.rstrip("/")
        if not value:
            return None
        return cls(
            pattern=value.replace("\\", "/"),
            negated=negated,
            directory_only=directory_only,
            anchored=anchored,
            has_slash="/" in value,
        )

    def matches(self, rel_path: str, is_dir: bool) -> bool:
        rel = _normalize_rel(rel_path)
        if not rel:
            return False
        if self.directory_only:
            return self._matches_directory(rel, is_dir)
        if not self.has_slash and not self.anchored:
            return fnmatch.fnmatch(Path(rel).name, self.pattern)
        return fnmatch.fnmatch(rel, self.pattern)

    def _matches_directory(self, rel: str, is_dir: bool) -> bool:
        if not self.has_slash and not self.anchored:
            parts = rel.split("/")
            candidates = parts if is_dir else parts[:-1]
            return any(fnmatch.fnmatch(part, self.pattern) for part in candidates)
        return rel == self.pattern or rel.startswith(self.pattern + "/")


class IgnoreRules:
    def __init__(
        self,
        root: Path,
        gitignore_patterns: list[str] | None = None,
        runtime_patterns: list[str] | None = None,
    ) -> None:
        self.root = root.resolve()
        self.gitignore_patterns = gitignore_patterns or read_gitignore_patterns(self.root)
        self.runtime_patterns = runtime_patterns or []
        self.rules = _parse_rules(self.gitignore_patterns + self.runtime_patterns)

    @classmethod
    def from_root(
        cls,
        root: Path,
        runtime_patterns: list[str] | None = None,
    ) -> "IgnoreRules":
        return cls(root, runtime_patterns=runtime_patterns)

    def is_ignored(self, path: Path, is_dir: bool) -> bool:
        try:
            resolved = path.resolve()
        except OSError:
            return True
        if not is_relative_to(resolved, self.root):
            return True
        rel = resolved.relative_to(self.root).as_posix()
        return self.is_ignored_rel(rel, is_dir)

    def is_ignored_rel(self, rel_path: str, is_dir: bool) -> bool:
        rel = _normalize_rel(rel_path)
        if not rel:
            return False
        if self._is_default_excluded(rel, is_dir):
            return True
        ignored = False
        for rule in self.rules:
            if rule.matches(rel, is_dir):
                ignored = not rule.negated
        return ignored

    def should_prune_dir(self, path: Path) -> bool:
        try:
            rel = path.resolve().relative_to(self.root).as_posix()
        except (OSError, ValueError):
            return True
        if not self.is_ignored_rel(rel, True):
            return False
        return not self._has_negated_descendant(rel)

    def fingerprint(self) -> str:
        digest = hashlib.sha1()
        for value in sorted(DEFAULT_EXCLUDE_DIRS):
            digest.update(f"dir:{value}\n".encode("utf-8"))
        for value in sorted(DEFAULT_EXCLUDE_FILE_PATTERNS):
            digest.update(f"file:{value}\n".encode("utf-8"))
        for value in self.gitignore_patterns:
            digest.update(f"git:{value}\n".encode("utf-8"))
        for value in self.runtime_patterns:
            digest.update(f"runtime:{value}\n".encode("utf-8"))
        return digest.hexdigest()

    def status(self) -> dict[str, object]:
        return {
            "default_exclude_dirs": sorted(DEFAULT_EXCLUDE_DIRS),
            "default_exclude_file_patterns": sorted(DEFAULT_EXCLUDE_FILE_PATTERNS),
            "gitignore_patterns": self.gitignore_patterns,
            "runtime_patterns": self.runtime_patterns,
            "fingerprint": self.fingerprint(),
        }

    def _is_default_excluded(self, rel: str, is_dir: bool) -> bool:
        parts = rel.split("/")
        if any(part in DEFAULT_EXCLUDE_DIRS for part in parts):
            return True
        if is_dir:
            return False
        name = Path(rel).name
        return any(
            fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(rel, pattern)
            for pattern in DEFAULT_EXCLUDE_FILE_PATTERNS
        )

    def _has_negated_descendant(self, rel: str) -> bool:
        prefix = _normalize_rel(rel).rstrip("/") + "/"
        return any(
            rule.negated and rule.pattern.startswith(prefix)
            for rule in self.rules
        )


def read_gitignore_patterns(root: Path) -> list[str]:
    path = root / ".gitignore"
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []


def ignore_fingerprint(root: Path, runtime_patterns: list[str] | None = None) -> str:
    return IgnoreRules.from_root(root, runtime_patterns).fingerprint()


def _parse_rules(patterns: list[str]) -> list[IgnoreRule]:
    rules: list[IgnoreRule] = []
    for pattern in patterns:
        rule = IgnoreRule.parse(pattern)
        if rule is not None:
            rules.append(rule)
    return rules


def _normalize_rel(path: str) -> str:
    return path.replace("\\", "/").strip("/")
